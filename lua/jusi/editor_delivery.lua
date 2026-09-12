local protocol = require("jusi.protocol")
local M = {}
local Delivery = {}
Delivery.__index = Delivery

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
  if record.file then record.file:close(); record.file = nil end
  if record.path then os.remove(record.path); record.path = nil end
end

function Delivery:finish(action, record, outcome)
  discard(record)
  record.outcome = outcome
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

function Delivery:request(action, attempt)
  if self.closed then return end
  local c = self.controller
  if not protocol.validate_editor_action_metadata(action) or action.editor_id ~= c.editor_id then return end
  local record = self.seen[action.action_id]
  if record and record.outcome then self:ack(action, record); return end
  if record and record.fetching or c.transport_state ~= "connected" then return end
  record = record or {}
  self.seen[action.action_id] = record
  if not self:owns(action) then
    self:finish(action, record, "failed")
    return
  end
  record.fetching = true
  local epoch, started = c.supervisor_id, vim.uv.hrtime()
  c.transport:request("GET", "/v1/editor-actions/" .. action.action_id .. "?editor_id=" .. c.editor_id .. "&offset=" .. (record.offset or 0),
    nil, { operation = "editor_action", trace_id = action.trace_id }, function(response, failure)
      record.fetching = false
      if self.closed or record.cancelled then discard(record); return end
      if failure then
        discard(record)
        self.seen[action.action_id] = nil
        if failure.layer == "frontend_transport" and (attempt or 0) < 3 then
          vim.defer_fn(function() self:request(action, (attempt or 0) + 1) end, 250)
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
              local ok, result = pcall(self.apply, record.header, action, record.path)
              delivered = ok and result ~= nil and result ~= false
            end
          end
        else
          local ok, result = pcall(self.apply, response.content, action)
          delivered = ok and result ~= nil and result ~= false
        end
      end
      -- Store the outcome before sending the acknowledgment. Replays only ack.
      self:finish(action, record, delivered and "delivered" or "failed")
    end)
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

function M.new(controller, apply)
  local self = setmetatable({ controller = controller, apply = apply, seen = {}, completed = {} }, Delivery)
  self.exit_autocmd = vim.api.nvim_create_autocmd("VimLeavePre", { once = true, callback = function() self:close() end })
  return self
end
return M
