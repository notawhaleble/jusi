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
local event_scenario = read_json("protocol/fixtures/v1/scenarios/event-payloads.json")
for _, fixture_event in ipairs(event_scenario.events) do
  local fixture_ok, fixture_error = protocol.validate_event(fixture_event)
  assert(fixture_ok, fixture_error)
end
local malformed_output = read_json("protocol/fixtures/v1/invalid/event-output-missing-data.json")
local malformed_output_ok = protocol.validate_event(malformed_output)
assert(not malformed_output_ok, "malformed output payload must be rejected")

local health = read_json("protocol/fixtures/v1/valid/health-response.json")
local health_ok, health_error = protocol.validate_health_response(health)
assert(health_ok, health_error)
local invalid_health = read_json("protocol/fixtures/v1/invalid/health-invalid-window.json")
local invalid_health_ok = protocol.validate_health_response(invalid_health)
assert(not invalid_health_ok, "invalid health fixture must be rejected")

local plugin_catalog = read_json("protocol/fixtures/v1/valid/plugin-catalog.json")
local catalog_ok, catalog_error = protocol.validate_plugin_catalog(plugin_catalog)
assert(catalog_ok, catalog_error)
local invalid_catalog = read_json("protocol/fixtures/v1/invalid/plugin-catalog-duplicate-id.json")
local invalid_catalog_ok = protocol.validate_plugin_catalog(invalid_catalog)
assert(not invalid_catalog_ok, "duplicate plugin identities must be rejected")

local specs = vim.fn.glob("tests/frontend/*_spec.lua", false, true)
table.sort(specs)
for _, path in ipairs(specs) do
  dofile(path).run()
end
dofile("tests/frontend/benchmark.lua").run()

print("Lua frontend tests passed")
