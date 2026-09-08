local terminal = require("jusi.presentation.terminal")
local presentation_window = require("jusi.presentation.window")

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
  }
end

function Presentation:write(cell_id, output)
  vim.validate("cell_id", cell_id, "string")
  vim.validate("output", output, "table")
  if output.execution_id and self.retired_executions[output.execution_id] then return nil end
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

  local surface = self.surfaces[cell_id]
  if not surface then
    local identity = self.pending[cell_id] or {}
    local created, value = pcall(terminal.new, {
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
    presentation_window.show(surface.buf, {
      anchor_buf = self.notebook_buf,
      height = self.height,
      enter = false,
    })
  end
  local ok, message = surface:write(output.data)
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
  local surface = self.surfaces[cell_id]
  if surface and not surface.closed and vim.api.nvim_buf_is_valid(surface.buf) then
    return surface.buf
  end
  return nil
end

function Presentation:close_cell(cell_id)
  local surface = self.surfaces[cell_id]
  if surface then
    surface:close()
    self.surfaces[cell_id] = nil
  end
  self.pending[cell_id] = nil
end

function Presentation:retire_execution(cell_id, execution_id)
  self.retired_executions[execution_id] = true
  local surface = self.surfaces[cell_id]
  local pending = self.pending[cell_id]
  if (surface and surface.execution_id == execution_id)
      or (pending and pending.execution_id == execution_id) then
    self:close_cell(cell_id)
  end
end

function Presentation:close()
  local cell_ids = vim.tbl_keys(self.surfaces)
  for _, cell_id in ipairs(cell_ids) do
    self:close_cell(cell_id)
  end
  self.pending = {}
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
          execution_id = request.execution_id, media_type = "text/plain", data = request.prompt,
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
      self:write(cell_id, { execution_id = request.execution_id, media_type = "text/plain", data = echo .. "\r\n" })
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
    surfaces = {},
    retired_executions = {},
  }, Presentation)
end

M.Presentation = Presentation

return M
