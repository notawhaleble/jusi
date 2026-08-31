local protocol = require("jusi.protocol")

local function read_json(path)
  local file = assert(io.open(path, "r"))
  local content = file:read("*a")
  file:close()
  return vim.json.decode(content)
end

local scenario = read_json("protocol/fixtures/v1/scenarios/walking-skeleton.json")
for _, command in ipairs(scenario.commands) do
  local ok, err = protocol.validate_command(command, command.kind)
  assert(ok, err)
end

local invalid = read_json("protocol/fixtures/v1/invalid/command-missing-trace-id.json")
local ok = protocol.validate_command(invalid, invalid.kind)
assert(not ok, "invalid fixture must be rejected")

local event = read_json("protocol/fixtures/v1/valid/execution-output.json")
local event_ok, event_err = protocol.validate_event(event)
assert(event_ok, event_err)
assert(event.payload.data == "\27[31mred\27[0m", "ANSI output must remain unchanged")

local invalid_event = read_json("protocol/fixtures/v1/invalid/event-missing-sequence.json")
local invalid_event_ok = protocol.validate_event(invalid_event)
assert(not invalid_event_ok, "invalid event fixture must be rejected")

local specs = vim.fn.glob("tests/frontend/*_spec.lua", false, true)
table.sort(specs)
for _, path in ipairs(specs) do
  dofile(path).run()
end
dofile("tests/frontend/benchmark.lua").run()

print("Lua frontend tests passed")
