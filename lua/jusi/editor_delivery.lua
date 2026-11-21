local protocol = require("jusi.protocol")
local M = {}
local Delivery = {}
Delivery.__index = Delivery

function Delivery:report(action, record, message, reason, layer)
  if record.reported or record.outcome or self.closed or record.cancelled then return end
  record.reported = true
  local failure = type(message) == 'table' and vim.deepcopy(message) or {
    layer = layer or 'frontend_presentation', reason = reason or 'internal_error',
    message = tostring(message or 'Editor action delivery failed'), retryable = false,
  }
  failure.trace_id = action.trace_id
  failure.operation = failure.operation or 'editor_action'
  failure.scope = failure.scope or 'client'
  failure.resource = failure.resource or { kind = 'client', id = action.client_id }
  failure.details = vim.tbl_extend('force', failure.details or {}, {
    action_id = action.action_id, action = action.action, client_id = action.client_id,
    runtime_id = action.runtime_id, notebook_id = action.notebook_id,
  })
  local diagnostics = require('jusi.diagnostics')
  diagnostics.record(failure)
  if self.controller.on_failure then self.controller.on_failure(failure)
  else vim.notify(diagnostics.summary(failure), vim.log.levels.ERROR, { title = 'Jusi' }) end
end

function Delivery:owns(action)
  local c = self.controller
  local client = c.clients[action.client_id]
  return action.editor_id == c.editor_id and action.notebook_id == c.notebook.notebook_id
    and client and client.runtime_id == action.runtime_id and client.cell_id == action.cell_id
    and c.notebook:cell_by_id(action.cell_id) ~= nil
end

function Delivery:ack(action, record, attempt)
  local c = self.controller
  if c.transport_state ~= "connected" or record.acking then return end
  record.acking = true
  local command = c:_command("ack_editor_action", {
    action_id = action.action_id, editor_id = c.editor_id, outcome = record.outcome,
  })
  c:_request("POST", "/v1/editor-actions/" .. action.action_id, command, function(response, failure)
    record.acking = false
    if (not failure and protocol.validate_editor_action_ack(response)
        and response.delivery.action_id == action.action_id and response.delivery.outcome == record.outcome)
        or (failure and failure.layer ~= "frontend_transport") then record.confirmed = true end
    if failure and failure.layer == "frontend_transport" and (attempt or 0) < 3 then
      vim.defer_fn(function() self:ack(action, record, (attempt or 0) + 1) end, 250)
    end
  end)
end

local function discard(record)
  local cancel = record.cancel; record.cancel = nil
  if cancel then cancel() end
  if record.keepalive and not record.keepalive:is_closing() then record.keepalive:stop(); record.keepalive:close() end
  record.keepalive = nil
  if record.reply_file then record.reply_file:close(); record.reply_file = nil end
  if record.reply_path then os.remove(record.reply_path); record.reply_path = nil end
  if record.file then record.file:close(); record.file = nil end
  if record.path then os.remove(record.path); record.path = nil end
end

function Delivery:finish(action, record, outcome)
  if record.outcome then return end
  record.outcome = outcome
  discard(record)
  record.context = nil
  record.expires = vim.uv.hrtime() + 35e9
  self:ack(action, record)
  table.insert(self.completed, action.action_id)
  local index = 1
  while #self.completed > 256 and index <= #self.completed do
    local old = self.seen[self.completed[index]]
    if not old or old.confirmed or vim.uv.hrtime() >= old.expires then
      self.seen[table.remove(self.completed, index)] = nil
    else
      -- Keep unconfirmed outcomes through the service's bounded pending lifetime.
      index = index + 1
    end
  end
end

function Delivery:upload(action, record)
  if record.outcome then return end
  if self.closed or record.cancelled or not self:owns(action) then self:finish(action, record, 'failed'); return end
  local text = record.reply_file:read(65536)
  local eof = text == nil
  text = text or ''
  local offset = record.reply_offset or 0
  self.controller.transport:request('POST', '/v1/editor-actions/' .. action.action_id .. '/reply', {
    editor_id = self.controller.editor_id, offset = offset, text = text, eof = eof,
  }, { operation = 'editor_action', trace_id = action.trace_id }, function(response, failure)
    if self.closed or record.cancelled or record.outcome then discard(record); return end
    if failure or type(response) ~= 'table' or response.next_offset ~= offset + #text or response.eof ~= eof then
      self:report(action, record, failure or 'Invalid editor-action reply acknowledgment', 'protocol_violation', 'protocol')
      self:finish(action, record, 'failed'); return
    end
    record.reply_offset = offset + #text
    if eof then self:finish(action, record, 'delivered') else self:upload(action, record) end
  end)
end

function Delivery:apply_result(action, record, result)
  if type(result) ~= 'table' or type(result.start) ~= 'function' then return false end
  record.fetching = true -- event replay cannot start the same side effect twice
  record.keepalive = vim.uv.new_timer()
  record.keepalive:start(1000, 1000, vim.schedule_wrap(function()
    if record.outcome or self.closed then return end
    self.controller.transport:request('GET', '/v1/editor-actions/' .. action.action_id .. '?editor_id='
      .. self.controller.editor_id .. '&offset=0', nil, { operation = 'editor_action', trace_id = action.trace_id }, function(_, failure)
        if failure and failure.layer ~= 'frontend_transport' and not record.outcome then self:finish(action, record, 'failed') end
      end)
  end))
  local finished = false
  local ok, cancel = pcall(result.start, function(success, reply_path, message)
    finished = true
    if self.closed or record.cancelled or record.outcome or not self:owns(action) then
      if reply_path then os.remove(reply_path) end
      discard(record); return
    end
    if not success then
      self:report(action, record, message or 'Document presentation or source capture failed')
      self:finish(action, record, 'failed'); return
    end
    -- A successful view transfers ownership to its client slot.
    record.cancel = nil
    if reply_path then
      record.reply_path = reply_path
      record.reply_file = io.open(reply_path, 'rb')
      if record.reply_file then self:upload(action, record)
      else
        self:report(action, record, 'Cannot read staged editor-action reply')
        self:finish(action, record, 'failed')
      end
    else self:finish(action, record, 'delivered') end
  end)
  if not ok then self:report(action, record, cancel); self:finish(action, record, 'failed')
  elseif not finished then record.cancel = cancel end
  return true
end

function Delivery:request(action, attempt)
  if self.closed then return end
  local c = self.controller
  if not protocol.validate_editor_action_metadata(action) or action.editor_id ~= c.editor_id then return end
  local record = self.seen[action.action_id]
  if record and record.outcome then self:ack(action, record); return end
  if record and record.fetching or c.transport_state ~= "connected" then return end
  record = record or { client_id = action.client_id }
  self.seen[action.action_id] = record
  if not self:owns(action) then
    self:finish(action, record, "failed")
    return
  end
  if not record.captured then
    record.captured = true
    if self.capture then
      local ok, context = pcall(self.capture, action)
      if not ok then
        self:report(action, record, context)
        self:finish(action, record, 'failed'); return
      end
      record.context = context
    end
    if record.context == false then
      self:report(action, record, 'Source client window is unavailable', 'not_found')
      self:finish(action, record, "failed")
      return
    end
  end
  record.fetching = true
  local epoch, started = c.supervisor_id, vim.uv.hrtime()
  c.transport:request("GET", "/v1/editor-actions/" .. action.action_id .. "?editor_id=" .. c.editor_id .. "&offset=" .. (record.offset or 0),
    nil, { operation = "editor_action", trace_id = action.trace_id }, function(response, failure)
      record.fetching = false
      if self.closed or record.cancelled then discard(record); return end
      if failure then
        discard(record)
        if failure.layer == "frontend_transport" and (attempt or 0) < 3 then
          -- Restart content staging without changing the captured destination.
          record.offset, record.header = nil, nil
          vim.defer_fn(function() self:request(action, (attempt or 0) + 1) end, 250)
        else
          self:report(action, record, failure)
          self.seen[action.action_id] = nil
        end
        return
      end
      if c.supervisor_id ~= epoch or c.transport_state ~= "connected" then
        discard(record)
        self.seen[action.action_id] = nil
        return
      end
      local valid = protocol.validate_editor_action_fetch(response) and vim.deep_equal(response.action, action)
        and response.remaining_ms > (vim.uv.hrtime() - started) / 1e6
      local delivered = false
      if valid and self:owns(action) then
        if response.offset ~= nil then
          valid = response.offset == (record.offset or 0)
          local header = vim.tbl_extend("force", response.content, { text = "" })
          valid = valid and (not record.header or vim.deep_equal(record.header, header))
          if valid then
            record.header = header
            if not record.file then
              record.path = vim.fn.tempname()
              local fd = vim.uv.fs_open(record.path, "wx", 384)
              if fd then vim.uv.fs_close(fd); record.file = io.open(record.path, "wb") end
            end
            valid = record.file ~= nil and record.file:write(response.content.text) ~= nil
            if not valid then self:report(action, record, 'Cannot stage editor-action content') end
          end
          if valid then
            record.offset = response.next_offset
            if not response.eof then
              self:request(action)
              return
            end
            local closed = record.file:close(); record.file = nil
            local stat = vim.uv.fs_stat(record.path)
            if closed and stat and stat.size == record.offset then
              local ok, result, message = pcall(self.apply, record.header, action, record.path, record.context)
              if ok and self:apply_result(action, record, result) then return end
              delivered = ok and result ~= nil and result ~= false
              if not delivered then self:report(action, record, ok and message or result) end
            else
              self:report(action, record, 'Cannot finalize staged editor-action content')
            end
          end
        else
          local ok, result, message = pcall(self.apply, response.content, action, nil, record.context)
          if ok and self:apply_result(action, record, result) then return end
          delivered = ok and result ~= nil and result ~= false
          if not delivered then self:report(action, record, ok and message or result) end
        end
      end
      if not valid then self:report(action, record, 'Invalid, expired or unstaged editor-action content', 'protocol_violation', 'protocol') end
      -- Store the outcome before sending the acknowledgment. Replays only ack.
      self:finish(action, record, delivered and "delivered" or "failed")
    end)
end

function Delivery:close_client(client_id)
  for id, record in pairs(self.seen) do
    if record.client_id == client_id and not record.outcome then
      record.cancelled = true; discard(record); self.seen[id] = nil
    end
  end
end

function Delivery:disconnect()
  for id, record in pairs(self.seen) do
    if not record.outcome then
      record.cancelled = true
      discard(record)
      self.seen[id] = nil
    end
  end
end

function Delivery:close()
  self.closed = true
  self:disconnect()
  if self.exit_autocmd then
    pcall(vim.api.nvim_del_autocmd, self.exit_autocmd)
    self.exit_autocmd = nil
  end
end

function M.new(controller, apply, capture)
  local self = setmetatable({ controller = controller, apply = apply, capture = capture, seen = {}, completed = {} }, Delivery)
  self.exit_autocmd = vim.api.nvim_create_autocmd("VimLeavePre", { once = true, callback = function() self:close() end })
  return self
end
return M
