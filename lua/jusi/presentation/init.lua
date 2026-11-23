local terminal = require("jusi.presentation.terminal")
local presentation_window = require("jusi.presentation.window")

local web = require("jusi.presentation.web")
local M = {}
local Presentation = {}
Presentation.__index = Presentation

local function is_textual(media_type)
  return type(media_type) == "string" and media_type:sub(1, 5) == "text/"
end

function Presentation:_failure(operation, reason, message, cell_id, details)
  return {
    trace_id = "",
    layer = "frontend_presentation",
    operation = operation,
    reason = reason,
    message = message,
    retryable = false,
    scope = "cell",
    resource = { kind = "cell", id = cell_id },
    details = details or {},
  }
end

function Presentation:start_execution(cell_id, execution)
  vim.validate("cell_id", cell_id, "string")
  vim.validate("execution", execution, "table")
  self:close_cell(cell_id)
  self.pending[cell_id] = {
    execution_id = execution.execution_id,
    client_id = execution.client_id,
    kernel_id = execution.kernel_id,
  }
end

function Presentation:write_bundle(cell_id, output)
  local pending = self.bundles[cell_id]
  if not pending or pending.id ~= output.bundle_id then
    pending = { id = output.bundle_id, execution_id = output.execution_id, chunks = {}, size = 0 }
    self.bundles[cell_id] = pending
  end
  pending.size = pending.size + #output.data
  if pending.size > 16 * 1024 * 1024 then
    pending.chunks = {} -- Retain the rejected identity through its end marker.
    if output.bundle_end then self.bundles[cell_id] = nil end
    return nil, self:_failure("render_output", "capacity_exceeded", "MIME bundle exceeds 16 MiB", cell_id)
  end
  table.insert(pending.chunks, output.data)
  if not output.bundle_end then return nil end
  self.bundles[cell_id] = nil
  local decoded, bundle = pcall(vim.json.decode, table.concat(pending.chunks))
  if not decoded or type(bundle) ~= "table" then
    return nil, self:_failure("render_output", "protocol_violation", "Invalid MIME bundle", cell_id)
  end
  if output.output_kind == "update" then
    local source = self.pending[cell_id]
    if not source then return end
    for target, area in pairs(self.areas) do
      local owner = self.pending[target]
      if owner and owner.kernel_id == source.kernel_id and not area.recording_failed then
        local redraw = false
        for _, entry in ipairs(area.entries) do
          if entry.display_id == output.display_id then
            local had_text = entry.text ~= nil
            self:render_display(target, owner, entry, bundle, true)
            redraw = redraw or had_text or entry.text ~= nil
          end
        end
        if redraw then self:replay_text(target) end
      end
    end
    return
  end
  local area = self:output_area(cell_id)
  local entry = { display_id = output.display_id }
  table.insert(area.entries, entry)
  local surface = self:render_display(cell_id, output, entry, bundle, false)
  if entry.text then return self:write_text(cell_id, output, entry.text) end
  return surface
end

function Presentation:output_area(cell_id, input_echo)
  local area = self.areas[cell_id]
  if area and area.clear_on_append and not input_echo then
    self:clear_output(cell_id)
    area = nil
  end
  if not area then
    area = { entries = {} }
    self.areas[cell_id] = area
  end
  return area
end

function Presentation:render_display(cell_id, owner, entry, bundle, updating)
  local previous = entry.surface
  local old_windows = previous and vim.fn.win_findbuf(previous.buf) or {}
  local surface
  if web.available() then
    local anchor = self.notebook_buf and presentation_window.find(self.notebook_buf)
    local tab = anchor and vim.api.nvim_win_get_tabpage(anchor) or vim.api.nvim_get_current_tabpage()
    local text = self.surfaces[cell_id]
    local reuse = previous and previous.buf or (text and text.buf)
    if not updating then
      local rich = self.web_surfaces[cell_id] or {}
      for index = #rich, 1, -1 do
        if presentation_window.find(rich[index].buf, tab) then
          reuse = rich[index].buf
          break
        end
      end
    end
    local win = reuse and presentation_window.find(reuse, tab)
    if updating and reuse and not win then win = presentation_window.find(reuse) end
    local hide = updating and not win
    local ok, rendered = pcall(web.render, bundle, {
      notebook_id = self.notebook_id, cell_id = cell_id, execution_id = owner.execution_id,
      win = win or anchor, reuse_buf = not hide and reuse or nil, height = self.height,
    })
    if ok then
      surface = rendered
      if surface and hide then presentation_window.close_for_buffer(surface.buf) end
    elseif self.on_failure then
      self.on_failure(self:_failure("render_output", "internal_error", tostring(rendered), cell_id))
    end
  end
  entry.surface, entry.text = surface, nil
  if not surface then
    entry.text = type(bundle["text/plain"]) == "string" and bundle["text/plain"]
      or "[Rich output requires JusiWebBuffer]\r\n"
  end
  if previous then
    local replacement = surface
    if not replacement and #old_windows > 0 then
      replacement = self:write_text(cell_id, owner, "", true)
    end
    for _, window in ipairs(old_windows) do
      if replacement and vim.api.nvim_win_is_valid(window) and vim.api.nvim_win_get_buf(window) == previous.buf then
        vim.api.nvim_win_set_buf(window, replacement.buf)
      end
    end
    previous:close()
  end
  if previous or surface then
    local rich = {}
    for _, item in ipairs(self.areas[cell_id].entries) do
      if item.surface then table.insert(rich, item.surface) end
    end
    self.web_surfaces[cell_id] = rich
  end
  return surface
end

-- Small text runs stay in memory. Larger runs occupy contiguous file ranges.
-- Long streams do not require a second, unbounded copy in Lua memory.
local function record_text(area, data)
  local tail = area.entries[#area.entries]
  if not tail or not tail.stream then
    tail = { stream = true, text = "" }
    table.insert(area.entries, tail)
  end
  if tail.text and #tail.text + #data <= 8192 then
    tail.text = tail.text .. data
    return
  end
  if not area.file then area.file = assert(io.tmpfile()) end
  local offset = assert(area.file:seek("end"))
  if tail.text then
    assert(area.file:write(tail.text))
    tail.offset, tail.length, tail.text = offset, #tail.text, nil
  end
  assert(area.file:write(data))
  tail.length = tail.length + #data
end

function Presentation:replay_text(cell_id)
  local area, owner = self.areas[cell_id], self.pending[cell_id]
  local surface = self.surfaces[cell_id]
  -- Neovim keeps terminal scrollback across escape-sequence resets. Replace
  -- the buffer in its existing windows so removed lines also leave scrollback.
  if surface then
    vim.api.nvim_buf_set_name(surface.buf, "")
    self.surfaces[cell_id] = nil
    local replacement = self:write_text(cell_id, owner, "", true)
    if not replacement then
      self.surfaces[cell_id] = surface
      return
    end
    replacement.input_request_id = surface.input_request_id
    for _, window in ipairs(vim.fn.win_findbuf(surface.buf)) do
      vim.api.nvim_win_set_buf(window, replacement.buf)
    end
    surface:close()
  end
  for _, entry in ipairs(area.entries) do
    if entry.text then
      self:write_text(cell_id, owner, entry.text, true)
    elseif entry.offset then
      assert(area.file:seek("set", entry.offset))
      local remaining = entry.length
      while remaining > 0 do
        local chunk = assert(area.file:read(math.min(65536, remaining)))
        self:write_text(cell_id, owner, chunk, true)
        remaining = remaining - #chunk
      end
    end
  end
end

function Presentation:write(cell_id, output)
  vim.validate("cell_id", cell_id, "string")
  vim.validate("output", output, "table")
  if output.execution_id and self.retired_executions[output.execution_id] then return nil end
  local owner = self.pending[cell_id]
  if owner and output.execution_id and owner.execution_id ~= output.execution_id then return nil end
  if output.wait ~= nil then
    if output.wait then
      local area = self.areas[cell_id]
      if area then area.clear_on_append = true end
    else
      self:clear_output(cell_id)
    end
    return
  end
  if output.media_type == "application/vnd.jusi.mime-bundle.v1+json" then
    local surface, failure = self:write_bundle(cell_id, output)
    if failure and self.on_failure then self.on_failure(failure) end
    return surface, failure
  end
  if not is_textual(output.media_type) then
    local failure = self:_failure(
      "render_output",
      "unsupported",
      "no renderer is registered for media type " .. tostring(output.media_type),
      cell_id,
      { media_type = output.media_type }
    )
    if self.on_failure then
      self.on_failure(failure)
    end
    return nil, failure
  end

  local area = self:output_area(cell_id, output.input_echo)
  local surface, failure = self:write_text(cell_id, output, output.data)
  if surface and not area.recording_failed then
    local ok, err = pcall(record_text, area, output.data)
    if not ok then
      area.recording_failed = true
      failure = self:_failure("render_output", "internal_error", tostring(err), cell_id)
      if self.on_failure then self.on_failure(failure) end
    end
  end
  return surface, failure
end

function Presentation:write_text(cell_id, output, data, hidden)
  local surface = self.surfaces[cell_id]
  if not surface then
    local identity = self.pending[cell_id] or {}
    local window
    if not hidden and not self:buffer_for_cell(cell_id) then
      window = { anchor_buf = self.notebook_buf, height = self.height, enter = false }
    end
    local created, value = pcall(terminal.new, {
      window = window,
      notebook_id = self.notebook_id,
      cell_id = cell_id,
      execution_id = identity.execution_id or output.execution_id,
      client_id = identity.client_id or output.client_id,
    })
    if not created then
      local failure = self:_failure("render_output", "internal_error", tostring(value), cell_id)
      if self.on_failure then
        self.on_failure(failure)
      end
      return nil, failure
    end
    surface = value
    self.surfaces[cell_id] = surface
  end
  local ok, message = surface:write(data)
  if not ok then
    local failure = self:_failure("render_output", "internal_error", message, cell_id)
    if self.on_failure then
      self.on_failure(failure)
    end
    return nil, failure
  end
  return surface
end

function Presentation:buffer_for_cell(cell_id)
  local rich = self.web_surfaces[cell_id] or {}
  for index = #rich, 1, -1 do
    if not rich[index].closed and vim.api.nvim_buf_is_valid(rich[index].buf) then return rich[index].buf end
  end
  local surface = self.surfaces[cell_id]
  if surface and not surface.closed and vim.api.nvim_buf_is_valid(surface.buf) then
    return surface.buf
  end
  return nil
end

function Presentation:clear_output(cell_id)
  local area = self.areas[cell_id]
  if area and area.file then area.file:close() end
  self.areas[cell_id] = nil
  for _, rich in ipairs(self.web_surfaces[cell_id] or {}) do rich:close() end
  self.web_surfaces[cell_id], self.bundles[cell_id] = nil, nil
  local surface = self.surfaces[cell_id]
  if surface then
    surface:close()
    self.surfaces[cell_id] = nil
  end
end

function Presentation:close_cell(cell_id)
  self:clear_output(cell_id)
  self.pending[cell_id] = nil
end

function Presentation:retire_execution(cell_id, execution_id)
  self.retired_executions[execution_id] = true
  local surface = self.surfaces[cell_id]
  local pending = self.pending[cell_id]
  local rich = (self.web_surfaces[cell_id] or {})[1]
  if (rich and rich.execution_id == execution_id) or (surface and surface.execution_id == execution_id)
      or (pending and pending.execution_id == execution_id) then
    self:close_cell(cell_id)
  end
end

function Presentation:close()
  for cell_id in pairs(self.areas) do self:close_cell(cell_id) end
  for cell_id in pairs(self.web_surfaces) do self:close_cell(cell_id) end
  local cell_ids = vim.tbl_keys(self.surfaces)
  for _, cell_id in ipairs(cell_ids) do
    self:close_cell(cell_id)
  end
  self.pending = {}
  self.bundles = {}
end

function Presentation:controller_callbacks()
  return {
    on_execution_started = function(cell_id, execution)
      self:start_execution(cell_id, execution)
    end,
    on_input_requested = function(cell_id, request)
      if self.retired_executions[request.execution_id] then return end
      local surface = self.surfaces[cell_id]
      if surface and surface.execution_id ~= request.execution_id then
        self:start_execution(cell_id, request)
        surface = nil
      end
      if not surface or surface.input_request_id ~= request.input_request_id then
        surface = self:write(cell_id, {
          execution_id = request.execution_id, input_echo = true, media_type = "text/plain", data = request.prompt,
        })
        if surface then surface.input_request_id = request.input_request_id end
      end
      if surface then
        presentation_window.show(surface.buf, { anchor_buf = self.notebook_buf, height = self.height, enter = false })
      end
    end,
    on_input_replied = function(cell_id, request, value)
      if self.retired_executions[request.execution_id] then return end
      local surface = self.surfaces[cell_id]
      if not surface or surface.execution_id ~= request.execution_id
          or surface.input_request_id ~= request.input_request_id then return end
      local echo = request.password and "[input accepted]" or (value or "[input accepted]")
      self:write(cell_id, { execution_id = request.execution_id, input_echo = true, media_type = "text/plain", data = echo .. "\r\n" })
    end,
    on_output = function(cell_id, output)
      self:write(cell_id, output)
    end,
  }
end

function M.new(options)
  local opts = options or {}
  vim.validate("notebook_id", opts.notebook_id, "string")
  return setmetatable({
    notebook_id = opts.notebook_id,
    notebook_buf = opts.notebook_buf,
    height = opts.height or 12,
    on_failure = opts.on_failure,
    pending = {},
    areas = {},
    surfaces = {},
    web_surfaces = {},
    bundles = {},
    retired_executions = {},
  }, Presentation)
end

M.Presentation = Presentation

return M
