local M = {}

local function nonempty_string(value)
  return type(value) == "string" and value ~= ""
end

local function bounded_string(value, minimum, maximum)
  return type(value) == "string" and vim.fn.strchars(value) >= minimum and vim.fn.strchars(value) <= maximum
end

local function set(values)
  local result = {}
  for _, value in ipairs(values) do
    result[value] = true
  end
  return result
end

local command_fields = {
  start_kernel = { "notebook_id", "kernel_name" },
  execute = { "kernel_id", "notebook_id", "cell_id", "code" },
  interrupt = { "kernel_id", "execution_id" },
  submit_input = { "kernel_id", "execution_id", "input_request_id", "value" },
  stop_kernel = { "kernel_id" },
  complete = { "kernel_id", "notebook_id", "cell_id", "body" },
  followup = { "client_id", "body" },
  close_client = { "client_id" },
  interrupt_client = { "client_id", "operation_id" },
  editor_action = { "client_id", "action" },
  ack_editor_action = { "action_id", "editor_id", "outcome" },
  restart_notebook = { "runtime_id", "kernel_id", "notebook_id", "next_notebook_id", "kernel_name" },
}
local layers = set({ "protocol", "frontend_transport", "service", "supervisor", "kernel", "execution", "client", "plugin_discovery", "plugin_worker" })
local operations = set({ "service_start", "start_kernel", "stop_kernel", "complete", "followup", "close_client", "ack_editor_action", "interrupt_client", "editor_action", "restart_notebook", "execute", "run_terminal_surface", "interrupt", "submit_input", "cleanup", "inspect", "connect_events" })
local event_kinds = set({ "editor_action.requested", "service.ready", "operation.started", "operation.completed", "kernel.state_changed", "execution.started", "execution.output", "execution.input_requested", "execution.input_replied", "execution.completed", "client.created", "client.closed", "surface.created", "surface.closed", "failure.occurred" })
local resource_kinds = set({ "supervisor", "notebook_runtime", "kernel", "execution", "client", "surface", "plugin_discovery", "plugin_worker", "transport", "notebook", "cell" })
local failure_reasons = set({ "invalid_request", "unsupported", "not_found", "conflict", "unreachable", "timeout", "cancelled", "spawn_failed", "readiness_failed", "process_exited", "process_signalled", "channel_closed", "protocol_violation", "kernel_died", "execution_error", "interrupted", "plugin_error", "cleanup_incomplete", "capacity_exceeded", "internal_error" })
local failure_scopes = set({ "request", "transport", "execution", "cell", "client", "plugin_discovery", "plugin_worker", "kernel", "supervisor" })
local worker_operations = set({ "interrupt", "execute", "followup", "complete", "editor_action" })
local worker_failure_reasons = set({ "conflict", "invalid_request", "unsupported", "timeout", "cancelled", "plugin_error", "internal_error" })
local terminal_stream_kinds = set({ "attach", "attached", "resize", "resized", "failure" })
local terminal_stream_failure_reasons = set({ "busy", "cursor_expired", "not_found", "protocol_violation", "channel_closed" })
local terminal_stream_failure_operations = set({ "attach", "resize", "stream" })
local uint64_max_decimal = "18446744073709551615"

local function exact_fields(value, required, optional)
  for _, field in ipairs(required) do
    if value[field] == nil then return false, "payload missing field: " .. field end
  end
  local allowed = {}
  for _, field in ipairs(required) do allowed[field] = true end
  for _, field in ipairs(optional or {}) do allowed[field] = true end
  for field, _ in pairs(value) do
    if not allowed[field] then return false, "payload unknown field: " .. field end
  end
  return true
end

local function valid_resource(value)
  if type(value) ~= "table" or not resource_kinds[value.kind] or not nonempty_string(value.id) then return false end
  for field, _ in pairs(value) do if field ~= "kind" and field ~= "id" then return false end end
  return true
end

local function null(value)
  return value == nil or value == vim.NIL
end

local function valid_terminal_cursor(value)
  if type(value) ~= "string" or (value ~= "0" and not value:match("^[1-9][0-9]*$")) or #value > 20 then return false end
  return #value < 20 or value <= uint64_max_decimal
end

local function valid_terminal_geometry(message)
  for _, field in ipairs({ "rows", "columns" }) do
    local value = message[field]
    if type(value) ~= "number" or value % 1 ~= 0 or value < 1 or value > 65535 then return false end
  end
  return true
end

local function uint64_bytes_to_decimal(bytes)
  local decimal = { 0 }
  for index = 1, #bytes do
    local carry = bytes:byte(index)
    for digit = #decimal, 1, -1 do
      local value = decimal[digit] * 256 + carry
      decimal[digit] = value % 10
      carry = math.floor(value / 10)
    end
    while carry > 0 do
      table.insert(decimal, 1, carry % 10)
      carry = math.floor(carry / 10)
    end
  end
  return table.concat(decimal)
end

local function validate_client(client)
  local fields = { "client_id", "runtime_id", "kernel_id", "notebook_id", "cell_id", "execution_id", "plugin_worker_id", "plugin_id", "plugin_version", "family_id", "capabilities", "interaction", "created_at" }
  local ok, err = exact_fields(client, fields)
  if not ok then return false, err end
  for _, field in ipairs({ "client_id", "runtime_id", "kernel_id", "notebook_id", "cell_id", "execution_id", "plugin_worker_id", "plugin_id", "plugin_version", "family_id", "created_at" }) do
    if not nonempty_string(client[field]) then return false, "invalid client identity" end
  end
  local allowed_capabilities = set({ "execute", "followup", "complete", "interrupt", "editor_actions" })
  if type(client.capabilities) ~= "table" then return false, "invalid client capabilities" end
  local seen = {}
  for _, capability in ipairs(client.capabilities) do
    if not allowed_capabilities[capability] or seen[capability] then return false, "invalid client capabilities" end
    seen[capability] = true
  end
  if not ({ noninteractive = true, request_response = true, terminal_interactive = true })[client.interaction] then return false, "invalid client interaction" end
  return true
end

local function validate_input_request(value)
  if type(value) ~= "table" then return false, "input request must be an object" end
  local ok, err = exact_fields(value, { "input_request_id", "execution_id", "kernel_id", "notebook_id", "cell_id", "prompt", "password" })
  if not ok then return false, err end
  for _, field in ipairs({ "input_request_id", "execution_id", "kernel_id", "notebook_id", "cell_id" }) do
    if not nonempty_string(value[field]) then return false, "invalid input request identity" end
  end
  if type(value.prompt) ~= "string" or type(value.password) ~= "boolean" then return false, "invalid input prompt or password flag" end
  return true
end

local function validate_active_execution(execution)
  local fields = { "execution_id", "kernel_id", "notebook_id", "cell_id", "client_id", "outcome", "started_at", "completed_at" }
  local ok, err = exact_fields(execution, fields)
  if not ok then return false, err end
  for _, field in ipairs({ "execution_id", "kernel_id", "notebook_id", "cell_id", "started_at" }) do
    if not nonempty_string(execution[field]) then return false, "invalid execution identity" end
  end
  if not null(execution.client_id) and not bounded_string(execution.client_id, 3, 128) then return false, "invalid execution client identity" end
  if execution.outcome ~= "running" or not null(execution.completed_at) then return false, "health execution must be active" end
  return true
end

local function validate_surface(surface)
  if type(surface) ~= "table" then return false, "surface must be an object" end
  local fields = { "surface_id", "client_id", "runtime_id", "kind", "capabilities", "transport", "created_at" }
  local ok, err = exact_fields(surface, fields)
  if not ok then return false, err end
  for _, field in ipairs({ "surface_id", "client_id", "runtime_id", "created_at" }) do
    if not nonempty_string(surface[field]) then return false, "invalid surface identity" end
  end
  if surface.kind ~= "terminal" then return false, "invalid surface kind" end
  if type(surface.capabilities) ~= "table" or not vim.islist(surface.capabilities) then return false, "invalid surface capabilities" end
  local allowed_capabilities = set({ "input", "resize", "signal" })
  local capabilities = {}
  for _, capability in ipairs(surface.capabilities) do
    if not allowed_capabilities[capability] or capabilities[capability] then return false, "invalid surface capabilities" end
    capabilities[capability] = true
  end
  if not capabilities.input or not capabilities.resize then return false, "invalid surface capabilities" end
  if type(surface.transport) ~= "table" then return false, "surface transport must be an object" end
  local transport_ok, transport_error = exact_fields(surface.transport, { "kind", "endpoint", "subprotocol" })
  if not transport_ok then return false, transport_error end
  if surface.transport.kind ~= "websocket"
      or surface.transport.subprotocol ~= "jusi.terminal.v1"
      or surface.transport.endpoint ~= "/v1/surfaces/" .. surface.surface_id .. "/terminal" then
    return false, "invalid surface transport"
  end
  return true
end

local function validate_terminal_surface_request(request)
  if type(request) ~= "table" then return false, "core request must be an object" end
  local ok, err = exact_fields(request, { "request_id", "kind", "argv", "cwd", "environment_overrides", "capabilities" })
  if not ok then return false, err end
  if not bounded_string(request.request_id, 3, 128) or request.kind ~= "terminal_surface.create" then
    return false, "invalid core request identity or kind"
  end
  if type(request.argv) ~= "table" or not vim.islist(request.argv) or #request.argv < 1 or #request.argv > 128 then return false, "terminal argv must be a non-empty array" end
  for _, argument in ipairs(request.argv) do
    if not bounded_string(argument, 1, 4096) then return false, "terminal argv must contain bounded non-empty strings" end
  end
  if not null(request.cwd) and (not bounded_string(request.cwd, 1, 4096) or request.cwd:sub(1, 1) ~= "/") then return false, "terminal cwd must be null or absolute" end
  if type(request.environment_overrides) ~= "table" then return false, "terminal environment overrides must be an object" end
  local environment_count = 0
  for name, value in pairs(request.environment_overrides) do
    environment_count = environment_count + 1
    if not bounded_string(name, 1, 256) or type(value) ~= "string" or vim.fn.strchars(value) > 8192 then return false, "terminal environment overrides must contain bounded string names and values" end
  end
  if environment_count > 128 then return false, "too many terminal environment overrides" end
  if type(request.capabilities) ~= "table" or not vim.islist(request.capabilities) then return false, "invalid terminal capabilities" end
  local allowed = set({ "input", "resize", "signal" })
  local actual = {}
  for _, capability in ipairs(request.capabilities) do
    if not allowed[capability] or actual[capability] then return false, "invalid terminal capabilities" end
    actual[capability] = true
  end
  if not actual.input or not actual.resize then return false, "invalid terminal capabilities" end
  return true
end

local function validate_event_payload(event)
  local payload = event.payload
  local kind = event.kind
  local ok, err
  if kind == "editor_action.requested" then
    local valid, error = M.validate_editor_action_metadata(payload)
    if not valid then return false, error end
    if event.resource.kind ~= "client" or event.resource.id ~= payload.client_id or event.trace_id ~= payload.trace_id
        or event.operation ~= "editor_action" then return false, "editor action event identity mismatch" end
  elseif kind == "service.ready" then
    ok, err = exact_fields(payload, { "supervisor_id" })
    if not ok then return false, err end
    if not nonempty_string(payload.supervisor_id) or payload.supervisor_id ~= event.supervisor_id then return false, "service.ready supervisor identity mismatch" end
    if event.resource.kind ~= "supervisor" or event.resource.id ~= event.supervisor_id then return false, "service.ready resource mismatch" end
  elseif kind == "operation.started" or kind == "operation.completed" then
    ok, err = exact_fields(payload, { "operation_id", "trace_id", "kind", "outcome", "started_at", "completed_at" }, kind == "operation.completed" and { "failure_id", "cleanup" } or {})
    if not ok then return false, err end
    for _, field in ipairs({ "operation_id", "trace_id", "kind", "started_at" }) do if not nonempty_string(payload[field]) then return false, "invalid operation payload" end end
    if not operations[payload.kind] or payload.trace_id ~= event.trace_id then return false, "operation payload identity is invalid" end
    if kind == "operation.started" and (payload.outcome ~= "running" or not null(payload.completed_at)) then return false, "operation.started must be running and incomplete" end
    if kind == "operation.completed" and (not ({ succeeded = true, failed = true, cancelled = true })[payload.outcome] or not nonempty_string(payload.completed_at)) then return false, "operation.completed has invalid outcome or time" end
    if payload.failure_id ~= nil and not nonempty_string(payload.failure_id) then return false, "invalid failure_id" end
    if payload.cleanup ~= nil and type(payload.cleanup) ~= "table" then return false, "invalid cleanup" end
  elseif kind == "kernel.state_changed" then
    ok, err = exact_fields(payload, { "kernel_id", "previous_state", "state" })
    if not ok then return false, err end
    if not nonempty_string(payload.kernel_id) or not ({ off = true, on = true })[payload.previous_state] or not ({ off = true, on = true })[payload.state] then return false, "invalid kernel state payload" end
    if event.resource.kind ~= "kernel" or event.resource.id ~= payload.kernel_id then return false, "kernel event resource mismatch" end
  elseif kind == "execution.started" or kind == "execution.completed" then
    ok, err = exact_fields(payload, { "execution_id", "kernel_id", "notebook_id", "cell_id", "client_id", "outcome", "started_at", "completed_at" })
    if not ok then return false, err end
    for _, field in ipairs({ "execution_id", "kernel_id", "notebook_id", "cell_id", "started_at" }) do if not nonempty_string(payload[field]) then return false, "invalid execution payload" end end
    if not null(payload.client_id) and not bounded_string(payload.client_id, 3, 128) then return false, "invalid execution client identity" end
    local outcomes = { pending = true, running = true, succeeded = true, failed = true, interrupted = true, cancelled = true }
    if not outcomes[payload.outcome] then return false, "invalid execution outcome" end
    if kind == "execution.started" and (payload.outcome ~= "running" or not null(payload.completed_at)) then return false, "execution.started must be running and incomplete" end
    if kind == "execution.completed" and ((payload.outcome == "pending" or payload.outcome == "running") or not nonempty_string(payload.completed_at)) then return false, "execution.completed has invalid outcome or time" end
    if event.resource.kind ~= "execution" or event.resource.id ~= payload.execution_id then return false, "execution event resource mismatch" end
  elseif kind == "execution.input_requested" or kind == "execution.input_replied" then
    local ok, err
    if kind == "execution.input_requested" then
      ok, err = validate_input_request(payload)
    else
      ok, err = exact_fields(payload, { "execution_id", "input_request_id" })
      if not nonempty_string(payload.execution_id) or not nonempty_string(payload.input_request_id) then return false, "invalid input reply identity" end
    end
    if not ok then return false, err end
    if event.resource.kind ~= "execution" or event.resource.id ~= payload.execution_id then return false, "input event resource mismatch" end
  elseif kind == "execution.output" then
    ok, err = exact_fields(payload, { "execution_id", "client_id", "output_kind", "media_type", "data" })
    if not ok then return false, err end
    if not nonempty_string(payload.execution_id) or (not null(payload.client_id) and not bounded_string(payload.client_id, 3, 128)) or not nonempty_string(payload.media_type) or type(payload.data) ~= "string" or not ({ stdout = true, stderr = true, result = true, display = true })[payload.output_kind] then return false, "invalid execution output payload" end
    if event.resource.kind ~= "execution" or event.resource.id ~= payload.execution_id then return false, "execution output resource mismatch" end
  elseif kind == "client.created" then
    ok, err = validate_client(payload)
    if not ok then return false, err end
    if event.resource.kind ~= "client" or event.resource.id ~= payload.client_id then return false, "client event resource mismatch" end
  elseif kind == "client.closed" then
    ok, err = exact_fields(payload, { "client_id", "plugin_worker_id", "reason", "closed_at" }, { "failure_id" })
    if not ok then return false, err end
    if not nonempty_string(payload.client_id) or not nonempty_string(payload.plugin_worker_id) or not ({ explicit_close = true, fatal_failure = true, runtime_cleanup = true })[payload.reason] or not nonempty_string(payload.closed_at) or (payload.failure_id ~= nil and not bounded_string(payload.failure_id, 3, 128)) then return false, "invalid client close payload" end
    if event.resource.kind ~= "client" or event.resource.id ~= payload.client_id then return false, "client event resource mismatch" end
  elseif kind == "surface.created" then
    ok, err = validate_surface(payload)
    if not ok then return false, err end
    if event.resource.kind ~= "surface" or event.resource.id ~= payload.surface_id then return false, "surface event resource mismatch" end
  elseif kind == "surface.closed" then
    ok, err = exact_fields(payload, { "surface_id", "client_id", "reason", "closed_at" }, { "failure_id" })
    if not ok then return false, err end
    if not nonempty_string(payload.surface_id) or not nonempty_string(payload.client_id)
        or not ({ client_cleanup = true, fatal_failure = true })[payload.reason]
        or not nonempty_string(payload.closed_at)
        or (payload.failure_id ~= nil and not bounded_string(payload.failure_id, 3, 128)) then
      return false, "invalid surface close payload"
    end
    if event.resource.kind ~= "surface" or event.resource.id ~= payload.surface_id then return false, "surface event resource mismatch" end
  elseif kind == "failure.occurred" then
    ok, err = exact_fields(payload, { "failure_id", "trace_id", "layer", "operation", "reason", "message", "retryable", "scope", "resource", "occurred_at" }, { "process", "caused_by_failure_id", "details" })
    if not ok then return false, err end
    for _, field in ipairs({ "failure_id", "trace_id", "reason", "message", "scope", "occurred_at" }) do if not nonempty_string(payload[field]) then return false, "invalid failure payload" end end
    if payload.trace_id ~= event.trace_id or not layers[payload.layer] or not operations[payload.operation] or not failure_reasons[payload.reason] or not failure_scopes[payload.scope] or type(payload.retryable) ~= "boolean" or not valid_resource(payload.resource) or not vim.deep_equal(payload.resource, event.resource) then return false, "invalid failure origin or resource" end
    if payload.details ~= nil and type(payload.details) ~= "table" then return false, "invalid failure details" end
    if payload.process ~= nil and type(payload.process) ~= "table" then return false, "invalid failure process" end
    if payload.caused_by_failure_id ~= nil and not nonempty_string(payload.caused_by_failure_id) then return false, "invalid caused_by_failure_id" end
  end
  return true
end

function M.validate_command(command, expected_kind)
  if type(command) ~= "table" then
    return false, "command must be an object"
  end
  if command.protocol_version ~= 1 then
    return false, "protocol_version must be 1"
  end
  if command.kind ~= expected_kind then
    return false, "unexpected command kind"
  end
  if not nonempty_string(command.command_id) or not nonempty_string(command.trace_id) then
    return false, "command_id and trace_id are required"
  end
  local fields = command_fields[expected_kind]
  if fields == nil then
    return false, "unsupported command kind"
  end
  for _, field in ipairs(fields) do
    if type(command[field]) ~= "string" or (field ~= "code" and field ~= "value" and field ~= "body" and command[field] == "") then
      return false, field .. " must be a string"
    end
  end
  local allowed = {
    protocol_version = true,
    command_id = true,
    trace_id = true,
    kind = true,
    idempotency_key = true,
  }
  for _, field in ipairs(fields) do
    allowed[field] = true
  end
  if expected_kind == "complete" then
    allowed.cursor_pos, allowed.client_id = true, true
    local pos = command.cursor_pos
    if type(pos) ~= "number" or pos % 1 ~= 0 or pos < 0 or pos > vim.fn.strchars(command.body) then
      return false, "invalid completion cursor offset"
    end
    if command.client_id ~= nil and not nonempty_string(command.client_id) then
      return false, "invalid completion client identity"
    end
  end
  if expected_kind == "editor_action" then
    allowed.selection = true
    if not set({ "copy", "open", "show_diff" })[command.action] or type(command.selection) ~= "table"
        or (#command.selection > 0) then return false, "invalid editor action selection" end
  end
  if expected_kind == "ack_editor_action" and command.outcome ~= "delivered" and command.outcome ~= "failed" then
    return false, "invalid editor acknowledgment outcome"
  end
  for field, _ in pairs(command) do
    if not allowed[field] then
      return false, "unknown command field: " .. field
    end
  end
  return true
end

function M.validate_terminal_stream_control(message)
  if type(message) ~= "table" or message.protocol_version ~= 1 or not terminal_stream_kinds[message.kind] then
    return false, "invalid terminal stream control envelope"
  end
  if not bounded_string(message.surface_id, 3, 128) or not bounded_string(message.attachment_id, 3, 128) then
    return false, "invalid terminal stream identity"
  end
  local common = { "protocol_version", "kind", "surface_id", "attachment_id" }
  if message.kind == "attach" or message.kind == "attached" then
    local ok, err = exact_fields(message, vim.list_extend(vim.deepcopy(common), { "cursor", "rows", "columns" }),
      message.kind == "attach" and { "editor_id" } or {})
    if message.editor_id ~= nil and (not bounded_string(message.editor_id, 3, 128) or not message.editor_id:match("^[A-Za-z0-9_]+$")) then
      return false, "invalid editor recipient identity"
    end
    if not ok then return false, err end
    if not valid_terminal_cursor(message.cursor) then return false, "invalid terminal stream cursor" end
    if not valid_terminal_geometry(message) then return false, "invalid terminal geometry" end
  elseif message.kind == "resize" or message.kind == "resized" then
    local ok, err = exact_fields(message, vim.list_extend(vim.deepcopy(common), { "resize_id", "rows", "columns" }))
    if not ok then return false, err end
    if not bounded_string(message.resize_id, 3, 128) then return false, "invalid terminal resize identity" end
    if not valid_terminal_geometry(message) then return false, "invalid terminal geometry" end
  else
    local ok, err = exact_fields(message, vim.list_extend(vim.deepcopy(common), { "operation", "reason", "message", "retryable" }))
    if not ok then return false, err end
    if not terminal_stream_failure_operations[message.operation]
        or not terminal_stream_failure_reasons[message.reason]
        or not bounded_string(message.message, 1, 1000)
        or type(message.retryable) ~= "boolean" then
      return false, "invalid terminal stream failure"
    end
  end
  return true
end

function M.decode_terminal_output_frame(frame)
  if type(frame) ~= "string" or #frame < 10 then return nil, "terminal output frame is shorter than its header" end
  if frame:byte(1) ~= 1 or frame:byte(2) ~= 1 then return nil, "unsupported terminal output frame version or type" end
  return {
    cursor = uint64_bytes_to_decimal(frame:sub(3, 10)),
    data = frame:sub(11),
  }
end

function M.validate_event(event)
  if type(event) ~= "table" or event.protocol_version ~= 1 then
    return false, "invalid event envelope"
  end
  local required = {
    "event_id",
    "supervisor_id",
    "sequence",
    "occurred_at",
    "trace_id",
    "layer",
    "operation",
    "kind",
    "resource",
    "payload",
  }
  for _, field in ipairs(required) do
    if event[field] == nil then
      return false, "missing event field: " .. field
    end
  end
  for _, field in ipairs({ "event_id", "supervisor_id", "occurred_at", "trace_id", "layer", "operation", "kind" }) do
    if not nonempty_string(event[field]) then
      return false, field .. " must be a non-empty string"
    end
  end
  if type(event.sequence) ~= "number" or event.sequence < 1 or event.sequence % 1 ~= 0 then
    return false, "sequence must be a positive integer"
  end
  if type(event.resource) ~= "table" or not nonempty_string(event.resource.kind) or not nonempty_string(event.resource.id) then
    return false, "resource kind and id must be non-empty strings"
  end
  for field, _ in pairs(event.resource) do
    if field ~= "kind" and field ~= "id" then
      return false, "resource must contain exactly kind and id"
    end
  end
  if type(event.payload) ~= "table" then
    return false, "payload must be an object"
  end
  if not layers[event.layer] or not operations[event.operation] or not event_kinds[event.kind] or not valid_resource(event.resource) then
    return false, "unsupported event envelope value"
  end
  return validate_event_payload(event)
end

function M.validate_health_response(response)
  if type(response) ~= "table" or response.ok ~= true or response.status ~= "ready" then
    return false, "health response must be a ready object"
  end
  if not nonempty_string(response.supervisor_id) then
    return false, "supervisor_id must be a non-empty string"
  end
  local earliest = response.earliest_event_sequence
  local latest = response.event_sequence
  if type(earliest) ~= "number" or earliest < 1 or earliest % 1 ~= 0 then
    return false, "earliest_event_sequence must be a positive integer"
  end
  if type(latest) ~= "number" or latest < 0 or latest % 1 ~= 0 then
    return false, "event_sequence must be a non-negative integer"
  end
  if earliest > latest + 1 then
    return false, "event replay window is invalid"
  end
  local kernel = response.kernel
  if kernel ~= nil and kernel ~= vim.NIL then
    if type(kernel) ~= "table" or not nonempty_string(kernel.kernel_id) then
      return false, "kernel must contain a non-empty kernel_id"
    end
    if kernel.state ~= "off" and kernel.state ~= "on" then
      return false, "kernel.state must be off or on"
    end
  end
  if response.runtime == nil then return false, "runtime must be present in health response" end
  local runtime = response.runtime
  if runtime ~= vim.NIL then
    local runtime_fields = set({ "runtime_id", "notebook_id", "discovery_id", "kernel_id", "plugin_catalog" })
    for field, _ in pairs(runtime_fields) do if runtime[field] == nil then return false, "runtime missing field: " .. field end end
    for field, _ in pairs(runtime) do if not runtime_fields[field] and field ~= "palette" then return false, "unknown runtime field: " .. field end end
    for _, field in ipairs({ "runtime_id", "notebook_id", "discovery_id", "kernel_id" }) do if not nonempty_string(runtime[field]) then return false, "invalid runtime identity" end end
    local catalog_ok, catalog_error = M.validate_plugin_catalog(runtime.plugin_catalog)
    if not catalog_ok then return false, catalog_error end
    if runtime.palette ~= nil then
      if type(runtime.palette) ~= "table" then return false, "invalid runtime palette" end
      local magics = {}
      for _, plugin in ipairs(runtime.plugin_catalog.plugins) do
        for _, family in ipairs(plugin.families) do magics[family.magic_name] = true end
      end
      for magic, section in pairs(runtime.palette) do
        if not magics[magic] or type(section) ~= "table" then return false, "invalid palette magic" end
        for field in pairs(section) do if field ~= "entries" then return false, "invalid palette section" end end
        if type(section.entries) ~= "table" or not vim.islist(section.entries) then return false, "invalid palette entries" end
        local seen = {}
        for _, entry in ipairs(section.entries) do
          if type(entry) ~= "string" or entry == "" or entry:find("%s") or seen[entry] then return false, "invalid palette entry" end
          seen[entry] = true
        end
      end
    end
    if runtime.plugin_catalog.discovery_id ~= runtime.discovery_id then return false, "runtime discovery identity mismatch" end
    if kernel == nil or kernel == vim.NIL or runtime.kernel_id ~= kernel.kernel_id or runtime.notebook_id ~= kernel.notebook_id then return false, "runtime kernel ownership mismatch" end
  elseif kernel ~= nil and kernel ~= vim.NIL and kernel.state == "on" then
    return false, "an on kernel requires an authoritative runtime"
  end
  if type(response.clients) ~= "table" then return false, "clients must be present as an array" end
  local client_ids = {}
  local worker_ids = {}
  for _, client in ipairs(response.clients) do
    local client_ok, client_error = validate_client(client)
    if not client_ok then return false, client_error end
    if client_ids[client.client_id] or worker_ids[client.plugin_worker_id] then return false, "duplicate client or worker identity" end
    client_ids[client.client_id] = true
    worker_ids[client.plugin_worker_id] = true
    if runtime == vim.NIL or client.runtime_id ~= runtime.runtime_id or client.kernel_id ~= runtime.kernel_id or client.notebook_id ~= runtime.notebook_id then return false, "client runtime ownership mismatch" end
    local plugin
    for _, candidate in ipairs(runtime.plugin_catalog.plugins) do
      if candidate.plugin_id == client.plugin_id then plugin = candidate break end
    end
    if plugin == nil or plugin.plugin_version ~= client.plugin_version or plugin.interaction ~= client.interaction then return false, "client exact plugin identity mismatch" end
    local family
    for _, candidate in ipairs(plugin.families) do
      if candidate.family_id == client.family_id then family = candidate break end
    end
    if family == nil then return false, "client family identity mismatch" end
    local expected_capabilities = set(family.capabilities)
    local actual_capabilities = set(client.capabilities)
    for capability, _ in pairs(expected_capabilities) do if not actual_capabilities[capability] then return false, "client family capabilities mismatch" end end
    for capability, _ in pairs(actual_capabilities) do if not expected_capabilities[capability] then return false, "client family capabilities mismatch" end end
  end
  if type(response.executions) ~= "table" or not vim.islist(response.executions) or #response.executions > 1 then
    return false, "executions must be present with at most one active execution"
  end
  for _, execution in ipairs(response.executions) do
    local execution_ok, execution_error = validate_active_execution(execution)
    if not execution_ok then return false, execution_error end
    if runtime == vim.NIL or execution.kernel_id ~= runtime.kernel_id or execution.notebook_id ~= runtime.notebook_id then
      return false, "execution runtime ownership mismatch"
    end
  end
  if not null(response.pending_input) then
    local input_ok, input_error = validate_input_request(response.pending_input)
    if not input_ok then return false, input_error end
    if #response.executions ~= 1 then return false, "input request needs an active execution" end
    for _, field in ipairs({ "kernel_id", "execution_id", "notebook_id", "cell_id" }) do
      if response.pending_input[field] ~= response.executions[1][field] then return false, "input request execution ownership mismatch" end
    end
  end
  if type(response.surfaces) ~= "table" or not vim.islist(response.surfaces) then return false, "surfaces must be present as an array" end
  local surface_ids = {}
  for _, surface in ipairs(response.surfaces) do
    local surface_ok, surface_error = validate_surface(surface)
    if not surface_ok then return false, surface_error end
    if surface_ids[surface.surface_id] then return false, "duplicate surface identity" end
    surface_ids[surface.surface_id] = true
    local owner = nil
    for _, client in ipairs(response.clients) do
      if client.client_id == surface.client_id then owner = client break end
    end
    if owner == nil or surface.runtime_id ~= owner.runtime_id then return false, "surface client ownership mismatch" end
    if owner.interaction ~= "terminal_interactive" then return false, "terminal surface owner is not interactive" end
  end
  local active = response.client_operations or {}
  if type(active) ~= "table" or not vim.islist(active) or #active > 1 then return false, "invalid client_operations" end
  for _, operation in ipairs(active) do
    local ok = type(operation) == "table" and exact_fields(operation, { "operation_id", "client_id", "kind" })
    if not ok then return false, "invalid active client operation" end
    local client_found = false
    for _, client in ipairs(response.clients) do if client.client_id == operation.client_id then client_found = true end end
    if not ok or not bounded_string(operation.operation_id, 1, 128) or not client_found
        or not set({ "followup", "complete", "editor_action" })[operation.kind] then return false, "invalid active client operation" end
  end

  local actions = response.editor_actions or {}
  if type(actions) ~= "table" or not vim.islist(actions) or #actions > 32 then return false, "invalid pending editor actions" end
  local seen_actions = {}
  for _, action in ipairs(actions) do
    if not M.validate_editor_action_metadata(action) or seen_actions[action.action_id] then return false, "invalid editor action" end
    seen_actions[action.action_id] = true
    local found = false
    for _, client in ipairs(response.clients) do
      if client.client_id == action.client_id and client.runtime_id == action.runtime_id
          and client.cell_id == action.cell_id and client.notebook_id == action.notebook_id then found = true end
    end
    if not found then return false, "editor action client ownership mismatch" end
  end

  return true
end

function M.validate_plugin_catalog(catalog)
  if type(catalog) ~= "table" or catalog.protocol_version ~= 1 or catalog.catalog_version ~= 1 or not bounded_string(catalog.discovery_id, 3, 128) or type(catalog.plugins) ~= "table" then
    return false, "invalid plugin catalog envelope"
  end
  local top = { protocol_version = true, catalog_version = true, discovery_id = true, plugins = true }
  for field, _ in pairs(catalog) do if not top[field] then return false, "unknown plugin catalog field: " .. field end end
  local ids = {}
  local capabilities = set({ "execute", "followup", "complete", "interrupt", "editor_actions" })
  local interactions = set({ "noninteractive", "request_response", "terminal_interactive" })
  local plugin_fields = set({ "plugin_id", "plugin_version", "distribution", "families", "kernel_extensions", "worker_entry_point", "media_types", "interaction" })
  local family_by_id = {}
  local family_by_magic = {}
  local kernel_modules = {}
  for _, plugin in ipairs(catalog.plugins) do
    if type(plugin) ~= "table" then return false, "plugin must be an object" end
    for field, _ in pairs(plugin_fields) do if plugin[field] == nil then return false, "plugin missing field: " .. field end end
    for field, _ in pairs(plugin) do if not plugin_fields[field] then return false, "unknown plugin field: " .. field end end
    if not bounded_string(plugin.plugin_id, 3, 128) or not bounded_string(plugin.plugin_version, 1, 128) or not bounded_string(plugin.distribution, 1, 256) or ids[plugin.plugin_id] then return false, "invalid or duplicate plugin identity" end
    ids[plugin.plugin_id] = true
    if not interactions[plugin.interaction] or (plugin.worker_entry_point ~= vim.NIL and not nonempty_string(plugin.worker_entry_point)) then return false, "invalid plugin interaction or worker" end
    for _, field in ipairs({ "kernel_extensions", "media_types" }) do
      if type(plugin[field]) ~= "table" then return false, "plugin list field must be an array" end
      local values = {}
      for _, value in ipairs(plugin[field]) do if not nonempty_string(value) or values[value] then return false, "plugin list values must be unique strings" end values[value] = true end
    end
    for _, module in ipairs(plugin.kernel_extensions) do
      if kernel_modules[module] then return false, "kernel extension module is claimed more than once" end
      kernel_modules[module] = true
    end
    if type(plugin.families) ~= "table" or #plugin.families == 0 then return false, "plugin families must not be empty" end
    local family_claims = {}
    for _, family in ipairs(plugin.families) do
      if type(family) ~= "table" or not bounded_string(family.family_id, 1, 128) or not nonempty_string(family.magic_name) or type(family.capabilities) ~= "table" then return false, "invalid plugin family" end
      local claim = family.family_id .. "\0" .. family.magic_name
      if family_claims[claim] or not family.magic_name:match("^[A-Za-z][A-Za-z0-9_-]*$") then return false, "duplicate or invalid family claim" end
      family_claims[claim] = true
      for field, _ in pairs(family) do if field ~= "family_id" and field ~= "magic_name" and field ~= "capabilities" and field ~= "presentation" and field ~= "provider_presentation" then return false, "unknown family field" end end
      local seen = {}
      for _, value in ipairs(family.capabilities) do if not capabilities[value] or seen[value] then return false, "invalid family capability" end seen[value] = true end
      for _, name in ipairs({ "presentation", "provider_presentation" }) do
        local profile = family[name]
        if profile ~= nil then
          if type(profile) ~= "table" or profile == vim.NIL or vim.islist(profile) and #profile > 0 then return false, "invalid editing profile" end
          for field, value in pairs(profile) do
            if (field ~= "syntax" and field ~= "indent") or type(value) ~= "string" or #value > 64
              or value ~= "" and not value:match("^[a-z][a-z0-9_]*$") then return false, "invalid editing profile" end
          end
        end
      end
      local sorted_capabilities = vim.deepcopy(family.capabilities)
      table.sort(sorted_capabilities)
      local presentation = family.presentation or {}
      local descriptor = table.concat({
        family.magic_name,
        table.concat(sorted_capabilities, "\0"),
        presentation.syntax or "",
        presentation.indent or "",
      }, "\1")
      if family_by_id[family.family_id] and family_by_id[family.family_id] ~= descriptor then return false, "conflicting family claim" end
      if family_by_magic[family.magic_name] and family_by_magic[family.magic_name] ~= family.family_id then return false, "magic is claimed by incompatible families" end
      family_by_id[family.family_id] = descriptor
      family_by_magic[family.magic_name] = family.family_id
    end
  end
  return true
end

function M.validate_plugin_worker_message(message)
  if type(message) ~= "table" or message.protocol_version ~= 1 then return false, "invalid plugin worker envelope" end
  local kind = message.kind
  if not set({ "worker.ready", "worker.request", "worker.result", "worker.failure", "worker.rejected", "worker.shutdown", "worker.stopped" })[kind] then
    return false, "unsupported plugin worker message kind"
  end
  if not bounded_string(message.plugin_worker_id, 3, 128) then return false, "invalid plugin worker identity" end
  if kind == "worker.ready" then
    local ok, err = exact_fields(message, { "protocol_version", "kind", "plugin_worker_id", "runtime_id", "plugin_id", "family_id", "client_id", "execution_id", "pid" })
    if not ok then return false, err end
    for _, field in ipairs({ "runtime_id", "plugin_id", "client_id", "execution_id" }) do
      if not bounded_string(message[field], 3, 128) then return false, "invalid worker ready identity" end
    end
    if not bounded_string(message.family_id, 1, 128) then return false, "invalid worker family identity" end
    if type(message.pid) ~= "number" or message.pid < 1 or message.pid % 1 ~= 0 then return false, "invalid worker pid" end
    return true
  end
  if not bounded_string(message.request_id, 3, 128) or not bounded_string(message.trace_id, 3, 128) then return false, "invalid worker request identity" end
  if kind == "worker.shutdown" or kind == "worker.stopped" then
    return exact_fields(message, { "protocol_version", "kind", "plugin_worker_id", "request_id", "trace_id" })
  end
  if not worker_operations[message.operation] then return false, "unsupported worker operation" end
  if kind == "worker.request" then
    if type(message.payload) ~= "table" then return false, "worker payload must be an object" end
    if message.operation == "interrupt" then
      local ok = exact_fields(message.payload, { "target_request_id" })
      if not ok or not nonempty_string(message.payload.target_request_id) then return false, "invalid interrupt target" end
    end
    return exact_fields(message, { "protocol_version", "kind", "plugin_worker_id", "request_id", "trace_id", "operation", "payload" })
  elseif kind == "worker.result" then
    if type(message.result) ~= "table" then return false, "worker result must be an object" end
    local result_ok, result_error = exact_fields(message, { "protocol_version", "kind", "plugin_worker_id", "request_id", "trace_id", "operation", "result", "core_requests" })
    if not result_ok then return false, result_error end
    if type(message.core_requests) ~= "table" or not vim.islist(message.core_requests) then return false, "worker core_requests must be an array" end
    local request_ids = {}
    for _, request in ipairs(message.core_requests) do
      local request_ok, request_error = validate_terminal_surface_request(request)
      if not request_ok then return false, request_error end
      if request_ids[request.request_id] then return false, "duplicate core request identity" end
      request_ids[request.request_id] = true
    end
    return true
  end
  if type(message.failure) ~= "table" then return false, "worker failure must be an object" end
  local failure_ok, failure_error = exact_fields(message.failure, { "reason", "message", "retryable" }, { "details" })
  if not failure_ok then return false, failure_error end
  if not worker_failure_reasons[message.failure.reason] or not bounded_string(message.failure.message, 1, 1000) or type(message.failure.retryable) ~= "boolean" then return false, "invalid worker failure" end
  if message.failure.details ~= nil and type(message.failure.details) ~= "table" then return false, "invalid worker failure details" end
  return exact_fields(message, { "protocol_version", "kind", "plugin_worker_id", "request_id", "trace_id", "operation", "failure" })
end

local function validate_kernel_families(families)
  if type(families) ~= "table" or #families == 0 then return false, "plugin adapter families must not be empty" end
  local seen = {}
  for _, family in ipairs(families) do
    local ok, err = exact_fields(family, { "family_id", "magic_name" })
    if not ok then return false, err end
    if not bounded_string(family.family_id, 1, 128) or not family.magic_name:match("^[A-Za-z][A-Za-z0-9_-]*$") then return false, "invalid plugin adapter family" end
    local identity = family.family_id .. "\0" .. family.magic_name
    if seen[identity] then return false, "duplicate plugin adapter family" end
    seen[identity] = true
  end
  return true
end

function M.validate_plugin_kernel_message(message)
  if type(message) ~= "table" or message.protocol_version ~= 1 then return false, "invalid plugin kernel envelope" end
  if message.kind == "plugin.adapters_ready" then
    local ok, err = exact_fields(message, { "protocol_version", "kind", "adapters" })
    if not ok then return false, err end
    if type(message.adapters) ~= "table" then return false, "adapters must be an array" end
    local modules = {}
    for _, adapter in ipairs(message.adapters) do
      local adapter_ok, adapter_err = exact_fields(adapter, { "plugin_id", "plugin_version", "module", "families" })
      if not adapter_ok then return false, adapter_err end
      if not bounded_string(adapter.plugin_id, 3, 128) or not bounded_string(adapter.plugin_version, 1, 128) or not bounded_string(adapter.module, 1, 512) or modules[adapter.module] then return false, "invalid plugin adapter identity" end
      modules[adapter.module] = true
      local families_ok, families_err = validate_kernel_families(adapter.families)
      if not families_ok then return false, families_err end
    end
    return true
  elseif message.kind == "plugin.handoff" then
    local ok, err = exact_fields(message, { "protocol_version", "kind", "plugin_id", "plugin_version", "family_id", "magic_name", "payload" })
    if not ok then return false, err end
    if not bounded_string(message.plugin_id, 3, 128) or not bounded_string(message.plugin_version, 1, 128) or type(message.payload) ~= "table" then return false, "invalid plugin handoff" end
    return validate_kernel_families({ { family_id = message.family_id, magic_name = message.magic_name } })
  end
  return false, "unsupported plugin kernel message kind"
end

function M.validate_completion(result, cursor_pos)
  if type(result) ~= "table" or type(result.items) ~= "table" or not vim.islist(result.items) or #result.items > 500 then
    return false, "completion.items must be a bounded array"
  end
  for _, item in ipairs(result.items) do
    if type(item) ~= "table" then return false, "invalid completion item" end
    local first, last = item.start, item["end"]
    if type(first) ~= "number" or type(last) ~= "number" or first % 1 ~= 0 or last % 1 ~= 0
      or first < 0 or first > last or last > cursor_pos then return false, "completion range must lie before cursor" end
    if type(item.text) ~= "string" or vim.fn.strchars(item.text) > 65536 or item.text:find("%z") then
      return false, "invalid completion text"
    end
    for _, key in ipairs({ "label", "detail", "documentation", "kind" }) do
      if item[key] ~= nil and (type(item[key]) ~= "string" or vim.fn.strchars(item[key]) > 4096 or item[key]:find("%z")) then
        return false, "invalid completion metadata"
      end
    end
  end
  return true
end

function M.validate_editor_action(result, action)
  if type(result) ~= "table" or result.action ~= action or not set({ "copy", "open", "show_diff" })[action] then
    return false, "editor action does not match request"
  end
  local fields = action == "copy" and { "action", "text", "regtype" } or { "action", "text", "name", "filetype" }
  if action == "show_diff" then fields = { "action", "text", "before_bytes", "before_name", "after_name", "filetype" } end
  local ok, err = exact_fields(result, fields)
  if not ok then return false, err end
  if type(result.text) ~= "string" or result.text:find("%z") then return false, "invalid editor text" end
  if action == "copy" then
    if result.regtype ~= "v" and result.regtype ~= "V" then return false, "invalid register type" end
  else
    if action == "show_diff" and (type(result.before_bytes) ~= "number" or result.before_bytes < 0
        or result.before_bytes > 9007199254740991 or result.before_bytes ~= math.floor(result.before_bytes)) then
      return false, "invalid diff boundary"
    end
    for _, key in ipairs(action == "show_diff" and { "before_name", "after_name" } or { "name" }) do
      local name = result[key]
      if type(name) ~= "string" or vim.fn.strchars(name) < 1 or vim.fn.strchars(name) > 128
          or name:find("[%c/\\]") or name == "." or name == ".." then return false, "invalid filename hint" end
    end
    if type(result.filetype) ~= "string" or #result.filetype > 64
        or (result.filetype ~= "" and not result.filetype:match("^[a-z][a-z0-9_]*$")) then return false, "invalid filetype" end
  end
  return true
end

function M.validate_editor_action_metadata(value)
  if type(value) ~= "table" then return false, "invalid editor action metadata" end
  local fields = { "action_id", "client_id", "runtime_id", "notebook_id", "cell_id", "editor_id", "trace_id", "action" }
  local ok, err = exact_fields(value, fields)
  if not ok then return false, err end
  for _, key in ipairs(fields) do if not bounded_string(value[key], 1, 128) then return false, "invalid editor action identity" end end
  if value.action ~= "copy" and value.action ~= "open" and value.action ~= "show_diff" then return false, "invalid editor action type" end
  return true
end

function M.validate_application_action(value)
  if type(value) ~= "table" or value.protocol_version ~= 1 or not bounded_string(value.request_id, 3, 128) then
    return false, "invalid application action envelope"
  end
  if value.kind == "application.editor_action" or value.kind == "application.editor_action_begin" then
    local ok = exact_fields(value, { "protocol_version", "kind", "request_id", "content" })
    if not ok or type(value.content) ~= "table" then return false, "invalid application action request" end
    if value.kind == "application.editor_action_begin" and value.content.text ~= "" then return false, "invalid stream header" end
    return M.validate_editor_action(value.content, value.content.action)
  elseif value.kind == "application.editor_action_chunk" then
    return exact_fields(value, { "protocol_version", "kind", "request_id", "text", "eof" })
      and type(value.text) == "string" and #value.text <= 65536 and not value.text:find("%z")
      and type(value.eof) == "boolean" and (value.eof or #value.text > 0)
  elseif value.kind == "application.action_result" then
    local ok = exact_fields(value, { "protocol_version", "kind", "request_id", "action_id", "outcome", "reason" })
    if not ok or not bounded_string(value.action_id, 0, 128) or not bounded_string(value.reason, 0, 128)
        or not set({ "delivered", "failed", "cancelled", "unknown" })[value.outcome] then return false, "invalid action result" end
    return true
  end
  return false, "unknown application action kind"
end

function M.validate_editor_action_fetch(value)
  if type(value) ~= "table" or value.ok ~= true
      or not (exact_fields(value, { "ok", "action", "content", "remaining_ms" })
        or exact_fields(value, { "ok", "action", "content", "remaining_ms", "offset", "next_offset", "eof" }))
      or not M.validate_editor_action_metadata(value.action) then return false, "invalid action fetch response" end
  if type(value.remaining_ms) ~= "number" or value.remaining_ms ~= math.floor(value.remaining_ms)
      or value.remaining_ms < 0 or value.remaining_ms > 30000 then return false, "invalid delivery lifetime" end
  if not M.validate_editor_action(value.content, value.action.action) then return false, "invalid content" end
  if value.offset ~= nil then
    if type(value.offset) ~= "number" or value.offset < 0 or value.offset ~= math.floor(value.offset)
        or type(value.next_offset) ~= "number" or value.next_offset ~= value.offset + #value.content.text
        or type(value.eof) ~= "boolean" or #value.content.text > 65536
        or (not value.eof and value.next_offset == value.offset) then return false, "invalid text chunk" end
  end
  return true
end

function M.validate_editor_action_ack(value)
  if type(value) ~= "table" or value.ok ~= true or not exact_fields(value, { "ok", "delivery" })
      or type(value.delivery) ~= "table" then return false, "invalid acknowledgment response" end
  local d = value.delivery
  if not exact_fields(d, { "action_id", "outcome", "reason" }) or not bounded_string(d.action_id, 1, 128)
      or not bounded_string(d.reason, 0, 128) or (d.outcome ~= "delivered" and d.outcome ~= "failed") then
    return false, "invalid acknowledgment result"
  end
  return true
end

return M
