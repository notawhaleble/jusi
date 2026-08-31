local protocol = require("jusi.protocol")

local M = {}
local Controller = {}
Controller.__index = Controller

local id_counter = 0

local function new_id(prefix)
  id_counter = id_counter + 1
  return string.format("%s_%x_%x", prefix, vim.uv.hrtime(), id_counter)
end

function Controller:_command(kind, fields)
  local trace_id = new_id("trace")
  local command = {
    protocol_version = 1,
    command_id = new_id("cmd"),
    trace_id = trace_id,
    kind = kind,
  }
  for key, value in pairs(fields or {}) do
    command[key] = value
  end
  local ok, error_message = protocol.validate_command(command, kind)
  assert(ok, error_message)
  return command
end

function Controller:_request(method, path, command, callback)
  local on_complete = callback or function() end
  return self.transport:request(method, path, command, {
    operation = command.kind,
    trace_id = command.trace_id,
  }, on_complete)
end

function Controller:connect(callback)
  if self.event_connection and not self.event_connection.closed then
    if callback then
      callback(true)
    end
    return self.event_connection
  end
  self.transport_state = "connecting"
  self.event_connection = self.transport:connect_events(self.event_sequence, {
    on_open = function()
      self.transport_state = "connected"
      if callback then
        local ready_callback = callback
        callback = nil
        ready_callback(true)
      end
    end,
    on_event = function(event)
      self:_on_event(event)
      if callback and self.transport_state == "connected" then
        local ready_callback = callback
        callback = nil
        ready_callback(true)
      end
    end,
    on_error = function(failure)
      self.transport_state = "disconnected"
      self.last_transport_failure = failure
      if self.on_failure then
        self.on_failure(failure)
      end
      if callback then
        local ready_callback = callback
        callback = nil
        ready_callback(false, failure)
      end
    end,
    on_close = function()
      self.transport_state = "disconnected"
    end,
  })
  return self.event_connection
end

function Controller:_on_event(event)
  local ok, validation_error = protocol.validate_event(event)
  if not ok then
    local failure = {
      trace_id = event.trace_id or "",
      layer = "frontend_transport",
      operation = "connect_events",
      reason = "protocol_violation",
      message = validation_error,
      retryable = false,
      scope = "transport",
      resource = { kind = "transport", id = self.transport.transport_id },
    }
    self.transport_state = "disconnected"
    self.last_transport_failure = failure
    if self.event_connection then
      self.event_connection:close()
    end
    if self.on_failure then
      self.on_failure(failure)
    end
    return
  end

  if self.supervisor_id and event.supervisor_id ~= self.supervisor_id then
    self.transport_state = "disconnected"
    if self.event_connection then
      self.event_connection:close()
    end
    return
  end
  if event.sequence <= self.event_sequence then
    return
  end
  if event.sequence ~= self.event_sequence + 1 then
    self.transport_state = "disconnected"
    self.last_transport_failure = {
      trace_id = event.trace_id,
      layer = "frontend_transport",
      operation = "connect_events",
      reason = "protocol_violation",
      message = string.format("event sequence gap: expected %d, received %d", self.event_sequence + 1, event.sequence),
      retryable = true,
      scope = "transport",
      resource = { kind = "transport", id = self.transport.transport_id },
    }
    if self.event_connection then
      self.event_connection:close()
    end
    if self.on_failure then
      self.on_failure(self.last_transport_failure)
    end
    return
  end

  self.supervisor_id = event.supervisor_id
  self.event_sequence = event.sequence
  self.transport_state = "connected"
  if event.kind == "kernel.state_changed" then
    self.kernel_id = event.payload.kernel_id
    self.kernel_state = event.payload.state
  elseif event.kind == "execution.started" then
    self.executions[event.payload.execution_id] = {
      execution_id = event.payload.execution_id,
      cell_id = event.payload.cell_id,
      client_id = event.payload.client_id,
      outcome = event.payload.outcome,
    }
    if self.on_execution_started then
      self.on_execution_started(event.payload.cell_id, self.executions[event.payload.execution_id], event)
    end
  elseif event.kind == "execution.output" then
    local execution = self.executions[event.payload.execution_id]
    if execution and self.on_output then
      self.on_output(execution.cell_id, event.payload, event)
    end
  elseif event.kind == "execution.completed" then
    local execution = self.executions[event.payload.execution_id]
    if execution then
      execution.outcome = event.payload.outcome
      if self.on_execution_completed then
        self.on_execution_completed(execution.cell_id, execution, event)
      end
    end
  elseif event.kind == "failure.occurred" and self.on_failure then
    self.on_failure(event.payload)
  end
  if self.on_event then
    self.on_event(event)
  end
end

function Controller:start_kernel(callback)
  local command = self:_command("start_kernel", {
    notebook_id = self.notebook.notebook_id,
    kernel_name = self.kernel_name,
    idempotency_key = new_id("start"),
  })
  return self:_request("POST", "/v1/kernels", command, function(response, failure)
    if response then
      self.kernel_id = response.kernel.kernel_id
      self.kernel_state = response.kernel.state
    end
    if callback then
      callback(response, failure)
    end
  end)
end

function Controller:execute(cell_id, callback)
  local body, body_error = self.notebook:body(cell_id)
  if not body then
    if callback then
      callback(nil, {
        trace_id = "",
        layer = "frontend_model",
        operation = "execute",
        reason = "conflict",
        message = body_error,
        retryable = false,
        scope = "cell",
        resource = { kind = "cell", id = cell_id },
      })
    end
    return nil
  end
  if self.kernel_state ~= "on" or not self.kernel_id then
    if callback then
      callback(nil, {
        trace_id = "",
        layer = "frontend_model",
        operation = "execute",
        reason = "conflict",
        message = "no authoritatively on kernel is selected",
        retryable = false,
        scope = "request",
        resource = { kind = "cell", id = cell_id },
      })
    end
    return nil
  end
  local command = self:_command("execute", {
    kernel_id = self.kernel_id,
    notebook_id = self.notebook.notebook_id,
    cell_id = cell_id,
    code = table.concat(body, "\n"),
  })
  return self:_request("POST", "/v1/kernels/" .. self.kernel_id .. "/executions", command, callback)
end

function Controller:stop_kernel(callback)
  if not self.kernel_id then
    if callback then
      callback(nil, {
        trace_id = "",
        layer = "frontend_model",
        operation = "stop_kernel",
        reason = "conflict",
        message = "no authoritative kernel resource is selected",
        retryable = false,
        scope = "request",
        resource = { kind = "notebook", id = self.notebook.notebook_id },
      })
    end
    return nil
  end
  local kernel_id = self.kernel_id
  local command = self:_command("stop_kernel", {
    kernel_id = kernel_id,
    idempotency_key = new_id("stop"),
  })
  return self:_request("DELETE", "/v1/kernels/" .. kernel_id, command, function(response, failure)
    if response then
      self.kernel_state = response.kernel.state
    end
    if callback then
      callback(response, failure)
    end
  end)
end

function Controller:close()
  if self.event_connection then
    self.event_connection:close()
    self.event_connection = nil
  end
  self.transport_state = "disconnected"
end

function M.new(options)
  local opts = options or {}
  vim.validate("notebook", opts.notebook, "table")
  vim.validate("transport", opts.transport, "table")
  return setmetatable({
    notebook = opts.notebook,
    transport = opts.transport,
    kernel_name = opts.kernel_name or "python3",
    kernel_id = nil,
    kernel_state = "off",
    supervisor_id = nil,
    event_sequence = opts.event_sequence or 0,
    transport_state = "disconnected",
    executions = {},
    on_event = opts.on_event,
    on_execution_started = opts.on_execution_started,
    on_execution_completed = opts.on_execution_completed,
    on_output = opts.on_output,
    on_failure = opts.on_failure,
    last_transport_failure = nil,
    event_connection = nil,
  }, Controller)
end

M.Controller = Controller

return M
