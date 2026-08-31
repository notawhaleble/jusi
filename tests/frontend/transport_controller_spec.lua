local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")
local sse = require("jusi.transport.sse")

local M = {}

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
  end
end

local function event(sequence, kind, payload)
  return {
    protocol_version = 1,
    event_id = string.format("evt_%03d", sequence),
    supervisor_id = "sup_test",
    sequence = sequence,
    occurred_at = "2026-08-31T12:00:00Z",
    trace_id = "trace_test",
    layer = kind == "kernel.state_changed" and "kernel" or "execution",
    operation = kind == "service.ready" and "service_start" or (kind == "kernel.state_changed" and "start_kernel" or "execute"),
    kind = kind,
    resource = { kind = kind == "kernel.state_changed" and "kernel" or "execution", id = "resource_test" },
    payload = payload or {},
  }
end

local FakeTransport = {}
FakeTransport.__index = FakeTransport

function FakeTransport.new()
  return setmetatable({ transport_id = "trn_test", requests = {} }, FakeTransport)
end

function FakeTransport:connect_events(_, callbacks)
  self.event_callbacks = callbacks
  return {
    closed = false,
    close = function(connection)
      connection.closed = true
    end,
  }
end

function FakeTransport:request(method, path, payload, _, callback)
  table.insert(self.requests, { method = method, path = path, payload = payload })
  if payload.kind == "start_kernel" then
    callback({ ok = true, kernel = { kernel_id = "krn_test", state = "on" } }, nil)
  elseif payload.kind == "execute" then
    callback({ ok = true, execution = { execution_id = "exe_test", outcome = "succeeded" } }, nil)
  elseif payload.kind == "stop_kernel" then
    callback({ ok = true, kernel = { kernel_id = "krn_test", state = "off" }, cleanup = { result = "stopped" } }, nil)
  end
  return { wait = function() end }
end

local function test_fragmented_sse_parser()
  local records = {}
  local comments = {}
  local stream = sse.new(function(record)
    table.insert(records, record)
  end, function(comment)
    table.insert(comments, comment)
  end)
  stream:feed(": keepalive\r")
  stream:feed("\nid: evt_1\r\nev")
  stream:feed("ent: execution.output\r\ndata: {\"part\":")
  stream:feed("1}\r\n\r\ndata: first\ndata: second\n\n")
  equal(comments, { "keepalive" })
  equal(records, {
    { id = "evt_1", event = "execution.output", data = '{"part":1}' },
    { data = "first\nsecond" },
  })
end

local function test_controller_routes_identity_and_preserves_kernel_truth_on_gap()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "1 + 1", "╰──" })
  local model = notebook.attach(buf)
  local cell_id = model:ordered_cells()[1].id
  local transport = FakeTransport.new()
  local outputs = {}
  local failures = {}
  local controller = controller_module.new({
    notebook = model,
    transport = transport,
    on_output = function(output_cell_id, output)
      table.insert(outputs, { cell_id = output_cell_id, output = output })
    end,
    on_failure = function(failure)
      table.insert(failures, failure)
    end,
  })

  controller:connect()
  transport.event_callbacks.on_event(event(1, "service.ready", { supervisor_id = "sup_test" }))
  controller:start_kernel()
  equal(controller.kernel_state, "on")
  equal(transport.requests[1].payload.notebook_id, model.notebook_id)

  transport.event_callbacks.on_event(event(2, "kernel.state_changed", {
    kernel_id = "krn_test",
    previous_state = "off",
    state = "on",
  }))
  controller:execute(cell_id)
  equal(transport.requests[2].payload.cell_id, cell_id)
  equal(transport.requests[2].payload.code, "1 + 1")

  transport.event_callbacks.on_event(event(3, "execution.started", {
    execution_id = "exe_test",
    kernel_id = "krn_test",
    notebook_id = model.notebook_id,
    cell_id = cell_id,
    client_id = "cli_test",
    outcome = "running",
    started_at = "2026-08-31T12:00:00Z",
  }))
  transport.event_callbacks.on_event(event(4, "execution.output", {
    execution_id = "exe_test",
    client_id = "cli_test",
    output_kind = "result",
    media_type = "text/plain",
    data = "2",
  }))
  equal(outputs[1].cell_id, cell_id)
  equal(outputs[1].output.data, "2")

  transport.event_callbacks.on_event(event(6, "execution.completed", {
    execution_id = "exe_test",
    outcome = "succeeded",
  }))
  equal(controller.transport_state, "disconnected")
  equal(controller.kernel_state, "on", "a transport gap must not invent kernel death")
  equal(failures[1].layer, "frontend_transport")
  equal(failures[1].reason, "protocol_violation")

  controller:close()
  model:detach()
end

local function test_invalid_cell_is_rejected_before_transport()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "unclosed" })
  local model = notebook.attach(buf)
  local cell_id = model:ordered_cells()[1].id
  local transport = FakeTransport.new()
  local controller = controller_module.new({ notebook = model, transport = transport })
  controller.kernel_id = "krn_test"
  controller.kernel_state = "on"
  local failure
  controller:execute(cell_id, function(_, value)
    failure = value
  end)
  equal(#transport.requests, 0)
  equal(failure.layer, "frontend_model")
  equal(failure.scope, "cell")
  model:detach()
end

local function test_malformed_event_disconnects_without_changing_kernel_truth()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "1", "╰──" })
  local model = notebook.attach(buf)
  local transport = FakeTransport.new()
  local failure
  local controller = controller_module.new({
    notebook = model,
    transport = transport,
    on_failure = function(value)
      failure = value
    end,
  })
  controller.kernel_state = "on"
  controller.kernel_id = "krn_test"
  controller:connect()
  local malformed = event(1, "service.ready", { supervisor_id = "sup_test" })
  malformed.sequence = 1.5
  transport.event_callbacks.on_event(malformed)
  equal(controller.transport_state, "disconnected")
  equal(controller.kernel_state, "on")
  equal(failure.reason, "protocol_violation")
  model:detach()
end

function M.run()
  test_fragmented_sse_parser()
  test_controller_routes_identity_and_preserves_kernel_truth_on_gap()
  test_invalid_cell_is_rejected_before_transport()
  test_malformed_event_disconnects_without_changing_kernel_truth()
end

return M
