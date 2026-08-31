local M = {}

local command_fields = {
  start_kernel = { "notebook_id", "kernel_name" },
  execute = { "kernel_id", "notebook_id", "cell_id", "code" },
  stop_kernel = { "kernel_id" },
}

local function nonempty_string(value)
  return type(value) == "string" and value ~= ""
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
  return true
end

return M
