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
  stop_kernel = { "kernel_id" },
  restart_notebook = { "runtime_id", "kernel_id", "notebook_id", "next_notebook_id", "kernel_name" },
}
local layers = set({ "protocol", "frontend_transport", "service", "supervisor", "kernel", "execution", "client", "plugin_discovery", "plugin_worker" })
local operations = set({ "service_start", "start_kernel", "stop_kernel", "restart_notebook", "execute", "interrupt", "cleanup", "inspect", "connect_events" })
local event_kinds = set({ "service.ready", "operation.started", "operation.completed", "kernel.state_changed", "execution.started", "execution.output", "execution.completed", "failure.occurred" })
local resource_kinds = set({ "supervisor", "notebook_runtime", "kernel", "execution", "client", "plugin_discovery", "plugin_worker", "transport", "notebook", "cell" })
local failure_reasons = set({ "invalid_request", "unsupported", "not_found", "conflict", "unreachable", "timeout", "cancelled", "spawn_failed", "readiness_failed", "process_exited", "process_signalled", "channel_closed", "protocol_violation", "kernel_died", "execution_error", "interrupted", "plugin_error", "cleanup_incomplete", "capacity_exceeded", "internal_error" })
local failure_scopes = set({ "request", "transport", "execution", "cell", "client", "plugin_discovery", "plugin_worker", "kernel", "supervisor" })

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

local function validate_event_payload(event)
  local payload = event.payload
  local kind = event.kind
  local ok, err
  if kind == "service.ready" then
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
    for _, field in ipairs({ "execution_id", "kernel_id", "notebook_id", "cell_id", "client_id", "started_at" }) do if not nonempty_string(payload[field]) then return false, "invalid execution payload" end end
    local outcomes = { pending = true, running = true, succeeded = true, failed = true, interrupted = true, cancelled = true }
    if not outcomes[payload.outcome] then return false, "invalid execution outcome" end
    if kind == "execution.started" and (payload.outcome ~= "running" or not null(payload.completed_at)) then return false, "execution.started must be running and incomplete" end
    if kind == "execution.completed" and ((payload.outcome == "pending" or payload.outcome == "running") or not nonempty_string(payload.completed_at)) then return false, "execution.completed has invalid outcome or time" end
    if event.resource.kind ~= "execution" or event.resource.id ~= payload.execution_id then return false, "execution event resource mismatch" end
  elseif kind == "execution.output" then
    ok, err = exact_fields(payload, { "execution_id", "client_id", "output_kind", "media_type", "data" })
    if not ok then return false, err end
    if not nonempty_string(payload.execution_id) or not nonempty_string(payload.client_id) or not nonempty_string(payload.media_type) or type(payload.data) ~= "string" or not ({ stdout = true, stderr = true, result = true, display = true })[payload.output_kind] then return false, "invalid execution output payload" end
    if event.resource.kind ~= "execution" or event.resource.id ~= payload.execution_id then return false, "execution output resource mismatch" end
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
    if type(command[field]) ~= "string" or (field ~= "code" and command[field] == "") then
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
  for field, _ in pairs(command) do
    if not allowed[field] then
      return false, "unknown command field: " .. field
    end
  end
  return true
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
    for field, _ in pairs(runtime) do if not runtime_fields[field] then return false, "unknown runtime field: " .. field end end
    for _, field in ipairs({ "runtime_id", "notebook_id", "discovery_id", "kernel_id" }) do if not nonempty_string(runtime[field]) then return false, "invalid runtime identity" end end
    local catalog_ok, catalog_error = M.validate_plugin_catalog(runtime.plugin_catalog)
    if not catalog_ok then return false, catalog_error end
    if runtime.plugin_catalog.discovery_id ~= runtime.discovery_id then return false, "runtime discovery identity mismatch" end
    if kernel == nil or kernel == vim.NIL or runtime.kernel_id ~= kernel.kernel_id or runtime.notebook_id ~= kernel.notebook_id then return false, "runtime kernel ownership mismatch" end
  elseif kernel ~= nil and kernel ~= vim.NIL and kernel.state == "on" then
    return false, "an on kernel requires an authoritative runtime"
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
    if type(plugin.families) ~= "table" or #plugin.families == 0 then return false, "plugin families must not be empty" end
    local family_claims = {}
    for _, family in ipairs(plugin.families) do
      if type(family) ~= "table" or not bounded_string(family.family_id, 1, 128) or not nonempty_string(family.magic_name) or type(family.capabilities) ~= "table" then return false, "invalid plugin family" end
      local claim = family.family_id .. "\0" .. family.magic_name
      if family_claims[claim] or not family.magic_name:match("^[A-Za-z][A-Za-z0-9_-]*$") then return false, "duplicate or invalid family claim" end
      family_claims[claim] = true
      for field, _ in pairs(family) do if field ~= "family_id" and field ~= "magic_name" and field ~= "capabilities" and field ~= "presentation" then return false, "unknown family field" end end
      local seen = {}
      for _, value in ipairs(family.capabilities) do if not capabilities[value] or seen[value] then return false, "invalid family capability" end seen[value] = true end
      if family.presentation ~= nil then
        if type(family.presentation) ~= "table" then return false, "invalid family presentation" end
        for field, value in pairs(family.presentation) do if (field ~= "syntax" and field ~= "indent") or type(value) ~= "string" then return false, "invalid family presentation" end end
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

return M
