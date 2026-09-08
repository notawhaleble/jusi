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
    timeout_ms = command.kind == "execute" and 0 or nil,
  }, on_complete)
end

function Controller:_transport_failure(reason, message)
  return {
    trace_id = "",
    layer = "frontend_transport",
    operation = "connect_events",
    reason = reason,
    message = message,
    retryable = reason ~= "protocol_violation",
    scope = "transport",
    resource = { kind = "transport", id = self.transport.transport_id },
  }
end

function Controller:_accept_health_snapshot(response)
  local valid, validation_error = protocol.validate_health_response(response)
  if not valid then
    return nil, self:_transport_failure("protocol_violation", validation_error)
  end
  local kernel = response.kernel
  if kernel == vim.NIL then
    kernel = nil
  end
  local runtime = response.runtime
  if runtime == vim.NIL then
    runtime = nil
  end

  local changed_supervisor = self.supervisor_id ~= nil and self.supervisor_id ~= response.supervisor_id
  local first_connection = self.supervisor_id == nil
  local cursor_unavailable = self.event_sequence < response.earliest_event_sequence - 1
    or self.event_sequence > response.event_sequence
  local resynchronized = first_connection or changed_supervisor or cursor_unavailable
  self.supervisor_id = response.supervisor_id
  if resynchronized then
    self.event_sequence = response.event_sequence
    self.executions = {}
    self.pending_input = nil
    self.input_submissions = {}
    self.clients = {}
    self.surfaces = {}
    for _, client in ipairs(response.clients) do
      self.clients[client.client_id] = client
    end
    for _, execution in ipairs(response.executions) do
      self.executions[execution.execution_id] = {
        execution_id = execution.execution_id,
        kernel_id = execution.kernel_id,
        notebook_id = execution.notebook_id,
        cell_id = execution.cell_id,
        client_id = execution.client_id,
        outcome = execution.outcome,
      }
    end
    for _, surface in ipairs(response.surfaces) do
      self.surfaces[surface.surface_id] = surface
    end
    if kernel then
      self.kernel_id = kernel.kernel_id
      self.kernel_state = kernel.state
    else
      self.kernel_id = nil
      self.kernel_state = "off"
    end
    if runtime then
      self.runtime_id = runtime.runtime_id
      self.discovery_id = runtime.discovery_id
      self.plugin_catalog = runtime.plugin_catalog
      if self.on_catalog then self.on_catalog() end
    else
      self.runtime_id = nil
      self.discovery_id = nil
      self.plugin_catalog = nil
    end
    if self.on_resynchronized then
      self.on_resynchronized({
        reason = first_connection and "initial_snapshot" or (changed_supervisor and "supervisor_replaced" or "cursor_expired"),
        supervisor_id = self.supervisor_id,
        event_sequence = self.event_sequence,
        kernel_id = self.kernel_id,
        kernel_state = self.kernel_state,
      })
    end
  end
  if resynchronized and type(response.pending_input) == "table"
      and response.pending_input.notebook_id == self.notebook.notebook_id then
    self.pending_input = response.pending_input
    if self.notebook:cell_by_id(self.pending_input.cell_id) and self.on_input_requested then
      self.on_input_requested(self.pending_input.cell_id, self.pending_input)
    end
  end
  return true
end

function Controller:connect(callback)
  if self.event_connection and not self.event_connection.closed then
    if callback then
      callback(true)
    end
    return self.event_connection
  end
  self.transport_state = "connecting"
  self._connect_generation = self._connect_generation + 1
  local generation = self._connect_generation
  self.inspect_request = self.transport:request("GET", "/v1/health", nil, {
    operation = "inspect",
    trace_id = "",
  }, function(response, failure)
    if generation ~= self._connect_generation then
      return
    end
    if failure then
      self.transport_state = "disconnected"
      self.last_transport_failure = failure
      if self.on_failure then
        self.on_failure(failure)
      end
      if callback then
        callback(false, failure)
        callback = nil
      end
      return
    end
    local accepted, snapshot_failure = self:_accept_health_snapshot(response)
    if not accepted then
      self.transport_state = "disconnected"
      self.last_transport_failure = snapshot_failure
      if self.on_failure then
        self.on_failure(snapshot_failure)
      end
      if callback then
        callback(false, snapshot_failure)
        callback = nil
      end
      return
    end
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
      on_error = function(stream_failure)
        self.transport_state = "disconnected"
        self.last_transport_failure = stream_failure
        if self.on_failure then
          self.on_failure(stream_failure)
        end
        if callback then
          local ready_callback = callback
          callback = nil
          ready_callback(false, stream_failure)
        end
      end,
      on_close = function()
        self.transport_state = "disconnected"
      end,
    })
  end)
  return self.inspect_request
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
    local failure = self:_transport_failure(
      "protocol_violation",
      "event supervisor does not match the inspected supervisor epoch"
    )
    failure.trace_id = event.trace_id
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
    if self.kernel_state == "off" then self.pending_input = nil end
  elseif event.kind == "execution.started" and event.payload.notebook_id == self.notebook.notebook_id then
    self.executions[event.payload.execution_id] = {
      execution_id = event.payload.execution_id,
      kernel_id = event.payload.kernel_id,
      notebook_id = event.payload.notebook_id,
      cell_id = event.payload.cell_id,
      client_id = event.payload.client_id,
      outcome = event.payload.outcome,
    }
    if self.on_execution_started then
      self.on_execution_started(event.payload.cell_id, self.executions[event.payload.execution_id], event)
    end
  elseif event.kind == "execution.input_requested" then
    local request = event.payload
    local execution = self.executions[request.execution_id]
    if execution and execution.outcome == "running" and request.kernel_id == self.kernel_id
        and request.notebook_id == self.notebook.notebook_id and request.cell_id == execution.cell_id then
      self.pending_input = request
      if self.notebook:cell_by_id(request.cell_id) and self.on_input_requested then
        self.on_input_requested(request.cell_id, request, event)
      end
    end
  elseif event.kind == "execution.input_replied" then
    local submission = self.input_submissions[event.payload.input_request_id]
    self.input_submissions[event.payload.input_request_id] = nil
    local request = submission and submission.request or self.pending_input
    if request and request.input_request_id == event.payload.input_request_id
        and request.execution_id == event.payload.execution_id
        and self.notebook:cell_by_id(request.cell_id) and self.on_input_replied then
      self.on_input_replied(request.cell_id, request,
        submission and submission.trace_id == event.trace_id and submission.value or nil, event)
    end
    if self.pending_input and self.pending_input.input_request_id == event.payload.input_request_id then
      self.pending_input = nil
    end
  elseif event.kind == "execution.output" then
    local execution = self.executions[event.payload.execution_id]
    if execution and self.on_output then
      self.on_output(execution.cell_id, event.payload, event)
    end
  elseif event.kind == "execution.completed" then
    for request_id, submission in pairs(self.input_submissions) do
      if submission.request.execution_id == event.payload.execution_id then
        self.input_submissions[request_id] = nil
      end
    end
    if self.pending_input and self.pending_input.execution_id == event.payload.execution_id then
      self.pending_input = nil
    end
    local execution = self.executions[event.payload.execution_id]
    if execution then
      execution.outcome = event.payload.outcome
      execution.client_id = event.payload.client_id
      if self.on_execution_completed then
        self.on_execution_completed(execution.cell_id, execution, event)
      end
    end
  elseif event.kind == "client.created" then
    self.clients[event.payload.client_id] = event.payload
    if self.on_client_created then
      self.on_client_created(event.payload, event)
    end
  elseif event.kind == "client.closed" then
    local client = self.clients[event.payload.client_id]
    self.clients[event.payload.client_id] = nil
    for surface_id, surface in pairs(self.surfaces) do
      if surface.client_id == event.payload.client_id then
        self.surfaces[surface_id] = nil
        if self.on_surface_closed then
          self.on_surface_closed(surface, {
            surface_id = surface_id,
            client_id = event.payload.client_id,
            reason = "client_cleanup",
            failure_id = event.payload.failure_id,
            closed_at = event.payload.closed_at,
          }, event)
        end
      end
    end
    if self.on_client_closed then
      self.on_client_closed(client, event.payload, event)
    end
  elseif event.kind == "surface.created" then
    self.surfaces[event.payload.surface_id] = event.payload
    if self.on_surface_created then
      self.on_surface_created(event.payload, event)
    end
  elseif event.kind == "surface.closed" then
    local surface = self.surfaces[event.payload.surface_id]
    self.surfaces[event.payload.surface_id] = nil
    if self.on_surface_closed then
      self.on_surface_closed(surface, event.payload, event)
    end
  elseif event.kind == "failure.occurred" and self.on_failure then
    self.on_failure(event.payload)
  end
  if self.on_status_event then self.on_status_event(event) end
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
      self.runtime_id = response.runtime.runtime_id
      self.discovery_id = response.runtime.discovery_id
      self.plugin_catalog = response.runtime.plugin_catalog
      if self.on_catalog then self.on_catalog() end
    end
    if callback then
      callback(response, failure)
    end
  end)
end

function Controller:restart_notebook(next_notebook_id, callback)
  if not self.runtime_id or not self.kernel_id then
    if callback then
      callback(nil, {
        trace_id = "",
        layer = "frontend_model",
        operation = "restart_notebook",
        reason = "conflict",
        message = "no authoritative notebook runtime is selected",
        retryable = false,
        scope = "request",
        resource = { kind = "notebook", id = self.notebook.notebook_id },
      })
    end
    return nil
  end
  local command = self:_command("restart_notebook", {
    runtime_id = self.runtime_id,
    kernel_id = self.kernel_id,
    notebook_id = self.notebook.notebook_id,
    next_notebook_id = next_notebook_id,
    kernel_name = self.kernel_name,
    idempotency_key = new_id("restart"),
  })
  return self:_request("POST", "/v1/notebook-runtimes/" .. self.runtime_id .. "/restart", command, function(response, failure)
    if response then
      self.runtime_id = response.runtime.runtime_id
      self.discovery_id = response.runtime.discovery_id
      self.plugin_catalog = response.runtime.plugin_catalog
      if self.on_catalog then self.on_catalog() end
      self.kernel_id = response.kernel.kernel_id
      self.kernel_state = response.kernel.state
      self.executions = {}
      self.pending_input = nil
      self.input_submissions = {}
      self.clients = {}
      self.surfaces = {}
    elseif failure and failure.details and failure.details.teardown_completed then
      self.runtime_id = nil
      self.discovery_id = nil
      self.plugin_catalog = nil
      self.kernel_state = "off"
      self.executions = {}
      self.pending_input = nil
      self.input_submissions = {}
      self.clients = {}
      self.surfaces = {}
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

function Controller:complete(context, callback)
  local command = self:_command("complete", {
    kernel_id = context.kernel_id, notebook_id = context.notebook_id,
    cell_id = context.cell_id, client_id = context.client_id,
    body = context.body, cursor_pos = context.cursor_pos,
  })
  return self:_request("POST", "/v1/kernels/" .. context.kernel_id .. "/completions", command, callback)
end

function Controller:followup(cell_id, callback)
  local body, body_error = self.notebook:body(cell_id)
  local client, count = nil, 0
  for _, candidate in pairs(self.clients) do
    if candidate.cell_id == cell_id and candidate.notebook_id == self.notebook.notebook_id then
      client, count = candidate, count + 1
    end
  end
  local message = body_error
  if body and count ~= 1 then message = "cell must have exactly one live client for a followup" end
  if body and count == 1 and not vim.tbl_contains(client.capabilities, "followup") then
    message = "client does not support followups"
  end
  if message then
    if callback then callback(nil, {
      trace_id = "", layer = "frontend_model", operation = "followup", reason = "conflict",
      message = message, retryable = false, scope = "cell", resource = { kind = "cell", id = cell_id },
    }) end
    return nil
  end
  self.followup_submissions = self.followup_submissions or {}
  if self.followup_submissions[client.client_id] then
    if callback then callback(nil, {
      trace_id = "", layer = "frontend_model", operation = "followup", reason = "conflict",
      message = "a followup is already in flight for this client", retryable = false,
      scope = "client", resource = { kind = "client", id = client.client_id },
    }) end
    return nil
  end
  local submissions = self.followup_submissions
  submissions[client.client_id] = true
  local command = self:_command("followup", { client_id = client.client_id, body = table.concat(body, "\n") })
  return self:_request("POST", "/v1/clients/" .. client.client_id .. "/followups", command, function(response, failure)
    submissions[client.client_id] = nil
    if callback then callback(response, failure) end
  end)
end

function Controller:submit_input(cell_id, callback)
  local request = self.pending_input
  local body, body_error = self.notebook:body(cell_id)
  if not body or not request or request.cell_id ~= cell_id or request.kernel_id ~= self.kernel_id
      or self.input_submissions[request.input_request_id] then
    if callback then
      callback(nil, {
        trace_id = "", layer = "frontend_model", operation = "submit_input", reason = "conflict",
        message = body_error or "cell has no available kernel input request (a reply may already be in flight)", retryable = false,
        scope = "cell", resource = { kind = "cell", id = cell_id },
      })
    end
    return nil
  end
  local command = self:_command("submit_input", {
    kernel_id = request.kernel_id, execution_id = request.execution_id,
    input_request_id = request.input_request_id, value = table.concat(body, "\n"),
  })
  self.input_submissions[request.input_request_id] = { request = request, value = command.value, trace_id = command.trace_id }
  return self:_request("POST", "/v1/kernels/" .. request.kernel_id
    .. "/executions/" .. request.execution_id .. "/input", command, function(response, failure)
      if failure and failure.layer ~= "frontend_transport" then
        self.input_submissions[request.input_request_id] = nil
      end
      if response and self.pending_input == request then self.pending_input = nil end
      if callback then callback(response, failure) end
    end)
end

function Controller:interrupt(execution_id, callback)
  local execution = self.executions[execution_id]
  if not execution or execution.outcome ~= "running" or not self.kernel_id then
    if callback then
      callback(nil, {
        trace_id = "",
        layer = "frontend_model",
        operation = "interrupt",
        reason = "conflict",
        message = "no matching active execution is selected",
        retryable = false,
        scope = "request",
        resource = { kind = "execution", id = execution_id or "unknown" },
      })
    end
    return nil
  end
  local kernel_id = execution.kernel_id or self.kernel_id
  local command = self:_command("interrupt", {
    kernel_id = kernel_id,
    execution_id = execution_id,
    idempotency_key = new_id("interrupt"),
  })
  return self:_request(
    "POST",
    "/v1/kernels/" .. kernel_id .. "/executions/" .. execution_id .. "/interrupt",
    command,
    callback
  )
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

function Controller:close_client(client_id, callback)
  if not self.clients[client_id] then
    if callback then
      callback(nil, {
        trace_id = "",
        layer = "frontend_model",
        operation = "close_client",
        reason = "not_found",
        message = "no authoritative client resource is selected",
        retryable = false,
        scope = "request",
        resource = { kind = "client", id = client_id },
      })
    end
    return nil
  end
  local command = self:_command("close_client", {
    client_id = client_id,
    idempotency_key = new_id("close"),
  })
  return self:_request("DELETE", "/v1/clients/" .. client_id, command, callback)
end

function Controller:close()
  self._connect_generation = self._connect_generation + 1
  if self.inspect_request and self.transport_state == "connecting" then
    pcall(self.inspect_request.kill, self.inspect_request, 15)
  end
  self.inspect_request = nil
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
    runtime_id = nil,
    discovery_id = nil,
    plugin_catalog = nil,
    supervisor_id = nil,
    event_sequence = opts.event_sequence or 0,
    transport_state = "disconnected",
    executions = {},
    input_submissions = {},
    clients = {},
    surfaces = {},
    on_event = opts.on_event,
    on_execution_started = opts.on_execution_started,
    on_execution_completed = opts.on_execution_completed,
    on_output = opts.on_output,
    on_input_requested = opts.on_input_requested,
    on_input_replied = opts.on_input_replied,
    on_client_created = opts.on_client_created,
    on_client_closed = opts.on_client_closed,
    on_surface_created = opts.on_surface_created,
    on_surface_closed = opts.on_surface_closed,
    on_failure = opts.on_failure,
    on_resynchronized = opts.on_resynchronized,
    last_transport_failure = nil,
    event_connection = nil,
    inspect_request = nil,
    _connect_generation = 0,
  }, Controller)
end

M.Controller = Controller

return M
