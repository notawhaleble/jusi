local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")
local sse = require("jusi.transport.sse")

local M = {}
local FakeTransport

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
  end
end

local function event(sequence, kind, payload)
  local resource_kind = "execution"
  local resource_id = payload and payload.execution_id or "exe_test"
  if kind == "service.ready" then
    resource_kind = "supervisor"
    resource_id = "sup_test"
  elseif kind == "kernel.state_changed" then
    resource_kind = "kernel"
    resource_id = payload.kernel_id
  elseif kind == "client.created" or kind == "client.closed" then
    resource_kind = "client"
    resource_id = payload.client_id
  elseif kind == "surface.created" or kind == "surface.closed" then
    resource_kind = "surface"
    resource_id = payload.surface_id
  end
  return {
    protocol_version = 1,
    event_id = string.format("evt_%03d", sequence),
    supervisor_id = "sup_test",
    sequence = sequence,
    occurred_at = "2026-08-31T12:00:00Z",
    trace_id = "trace_test",
    layer = kind == "kernel.state_changed" and "kernel" or (((kind == "client.created" or kind == "client.closed" or kind == "surface.created" or kind == "surface.closed")) and "client" or "execution"),
    operation = kind == "service.ready" and "service_start" or (kind == "kernel.state_changed" and "start_kernel" or "execute"),
    kind = kind,
    resource = { kind = resource_kind, id = resource_id },
    payload = payload or {},
  }
end

local function test_controller_tracks_durable_client_lifecycle()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "%%sql", "╰──" })
  local model = notebook.attach(buf)
  local transport = FakeTransport.new()
  local created
  local closed
  local surface_created
  local surface_closed
  local controller = controller_module.new({
    notebook = model,
    transport = transport,
    on_client_created = function(client) created = client end,
    on_client_closed = function(client, close) closed = { client = client, close = close } end,
    on_surface_created = function(surface) surface_created = surface end,
    on_surface_closed = function(surface, close) surface_closed = { surface = surface, close = close } end,
  })
  controller:connect()
  local client = {
    client_id = "cli_plugin",
    runtime_id = "run_plugin",
    kernel_id = "krn_plugin",
    notebook_id = model.notebook_id,
    cell_id = model:ordered_cells()[1].id,
    execution_id = "exe_plugin",
    plugin_worker_id = "pwrk_plugin",
    plugin_id = "sqlite_provider",
    plugin_version = "1.0.0",
    family_id = "sql",
    capabilities = { "execute", "followup" },
    interaction = "terminal_interactive",
    created_at = "2026-09-01T08:00:00Z",
  }
  transport.event_callbacks.on_event(event(1, "client.created", client))
  equal(controller.clients.cli_plugin.plugin_id, "sqlite_provider")
  equal(created.client_id, "cli_plugin")
  local surface = {
    surface_id = "srf_plugin",
    client_id = "cli_plugin",
    runtime_id = "run_plugin",
    kind = "terminal",
    capabilities = { "input", "resize" },
    transport = {
      kind = "websocket",
      endpoint = "/v1/surfaces/srf_plugin/terminal",
      subprotocol = "jusi.terminal.v1",
    },
    created_at = "2026-09-01T08:00:01Z",
  }
  transport.event_callbacks.on_event(event(2, "surface.created", surface))
  equal(controller.surfaces.srf_plugin.client_id, "cli_plugin")
  equal(surface_created.surface_id, "srf_plugin")
  controller:close_client("cli_plugin")
  equal(transport.requests[2].method, "DELETE")
  equal(transport.requests[2].path, "/v1/clients/cli_plugin")
  equal(transport.requests[2].payload.kind, "close_client")
  equal(transport.requests[2].payload.client_id, "cli_plugin")
  transport.event_callbacks.on_event(event(3, "client.closed", {
    client_id = "cli_plugin",
    plugin_worker_id = "pwrk_plugin",
    reason = "fatal_failure",
    failure_id = "fail_plugin",
    closed_at = "2026-09-01T08:01:00Z",
  }))
  equal(controller.clients, {})
  equal(controller.surfaces, {})
  equal(closed.client.client_id, "cli_plugin")
  equal(closed.close.reason, "fatal_failure")
  equal(surface_closed.surface.surface_id, "srf_plugin")
  equal(surface_closed.close.reason, "client_cleanup")
  controller:close()
  model:detach()
end

local function test_controller_accepts_authoritative_surface_snapshot()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "%%sql", "╰──" })
  local model = notebook.attach(buf)
  local client = {
    client_id = "cli_snapshot",
    runtime_id = "run_snapshot",
    kernel_id = "krn_snapshot",
    notebook_id = model.notebook_id,
    cell_id = model:ordered_cells()[1].id,
    execution_id = "exe_snapshot",
    plugin_worker_id = "pwrk_snapshot",
    plugin_id = "sqlite_provider",
    plugin_version = "1.0.0",
    family_id = "sql",
    capabilities = { "execute", "followup" },
    interaction = "terminal_interactive",
    created_at = "2026-09-01T08:00:00Z",
  }
  local surface = {
    surface_id = "srf_snapshot",
    client_id = "cli_snapshot",
    runtime_id = "run_snapshot",
    kind = "terminal",
    capabilities = { "input", "resize", "signal" },
    transport = {
      kind = "websocket",
      endpoint = "/v1/surfaces/srf_snapshot/terminal",
      subprotocol = "jusi.terminal.v1",
    },
    created_at = "2026-09-01T08:00:01Z",
  }
  local transport = FakeTransport.new({
    ok = true,
    status = "ready",
    supervisor_id = "sup_test",
    earliest_event_sequence = 1,
    event_sequence = 0,
    kernel = { kernel_id = "krn_snapshot", notebook_id = model.notebook_id, state = "on" },
    runtime = {
      runtime_id = "run_snapshot",
      notebook_id = model.notebook_id,
      discovery_id = "discovery_snapshot",
      kernel_id = "krn_snapshot",
      plugin_catalog = {
        protocol_version = 1,
        catalog_version = 1,
        discovery_id = "discovery_snapshot",
        plugins = {
          {
            plugin_id = "sqlite_provider",
            plugin_version = "1.0.0",
            distribution = "jusi-sqlite",
            families = { { family_id = "sql", magic_name = "sql", capabilities = { "execute", "followup" } } },
            kernel_extensions = {},
            worker_entry_point = "jusi_sqlite:create_worker",
            media_types = { "application/x-jusi-terminal" },
            interaction = "terminal_interactive",
          },
        },
      },
    },
    executions = {
      {
        execution_id = "exe_active",
        kernel_id = "krn_snapshot",
        notebook_id = model.notebook_id,
        cell_id = model:ordered_cells()[1].id,
        client_id = vim.NIL,
        outcome = "running",
        started_at = "2026-09-01T08:00:02Z",
        completed_at = vim.NIL,
      },
    },
    clients = { client },
    surfaces = { surface },
  })
  local controller = controller_module.new({ notebook = model, transport = transport })
  controller:connect()
  equal(controller.clients.cli_snapshot.plugin_worker_id, "pwrk_snapshot")
  equal(controller.surfaces.srf_snapshot.client_id, "cli_snapshot")
  equal(controller.executions.exe_active.outcome, "running")
  controller:close()
  model:detach()
end

FakeTransport = {}
FakeTransport.__index = FakeTransport

function FakeTransport.new(health)
  return setmetatable({
    transport_id = "trn_test",
    requests = {},
    health = health or {
      ok = true,
      status = "ready",
      executions = {},
      clients = {},
      surfaces = {},
      supervisor_id = "sup_test",
      earliest_event_sequence = 1,
      event_sequence = 0,
      kernel = nil,
      runtime = vim.NIL,
    },
  }, FakeTransport)
end

function FakeTransport:connect_events(after, callbacks)
  self.connected_after = after
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
  if path == "/v1/health" then
    callback(vim.deepcopy(self.health), nil)
  elseif payload.kind == "start_kernel" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_test", state = "on" },
      runtime = {
        runtime_id = "run_test",
        discovery_id = "discovery_test",
        plugin_catalog = { protocol_version = 1, catalog_version = 1, discovery_id = "discovery_test", plugins = {} },
      },
    }, nil)
  elseif payload.kind == "execute" then
    callback({ ok = true, execution = { execution_id = "exe_test", outcome = "succeeded" } }, nil)
  elseif payload.kind == "stop_kernel" then
    callback({ ok = true, kernel = { kernel_id = "krn_test", state = "off" }, cleanup = { result = "stopped" } }, nil)
  elseif payload.kind == "close_client" then
    callback({ ok = true, client = { client_id = payload.client_id }, cleanup = { result = "stopped" } }, nil)
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
  equal(transport.requests[2].payload.notebook_id, model.notebook_id)

  transport.event_callbacks.on_event(event(2, "kernel.state_changed", {
    kernel_id = "krn_test",
    previous_state = "off",
    state = "on",
  }))
  controller:execute(cell_id)
  equal(transport.requests[3].payload.cell_id, cell_id)
  equal(transport.requests[3].payload.code, "1 + 1")

  transport.event_callbacks.on_event(event(3, "execution.started", {
    execution_id = "exe_test",
    kernel_id = "krn_test",
    notebook_id = model.notebook_id,
    cell_id = cell_id,
    client_id = "cli_test",
    outcome = "running",
    started_at = "2026-08-31T12:00:00Z",
    completed_at = vim.NIL,
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
    kernel_id = "krn_test",
    notebook_id = model.notebook_id,
    cell_id = cell_id,
    client_id = "cli_test",
    outcome = "succeeded",
    started_at = "2026-08-31T12:00:00Z",
    completed_at = "2026-08-31T12:00:01Z",
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
  controller.kernel_state = "on"
  controller.kernel_id = "krn_test"
  local malformed = event(1, "service.ready", { supervisor_id = "sup_test" })
  malformed.sequence = 1.5
  transport.event_callbacks.on_event(malformed)
  equal(controller.transport_state, "disconnected")
  equal(controller.kernel_state, "on")
  equal(failure.reason, "protocol_violation")
  model:detach()
end

local function test_supervisor_replacement_resynchronizes_from_authoritative_snapshot()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "1", "╰──" })
  local model = notebook.attach(buf)
  local transport = FakeTransport.new({
    ok = true,
    status = "ready",
    executions = {},
    clients = {},
    surfaces = {},
    supervisor_id = "sup_new",
    earliest_event_sequence = 1,
    event_sequence = 1,
    kernel = nil,
    runtime = vim.NIL,
  })
  local resynchronized
  local controller = controller_module.new({
    notebook = model,
    transport = transport,
    event_sequence = 8,
    on_resynchronized = function(value)
      resynchronized = value
    end,
  })
  controller.supervisor_id = "sup_old"
  controller.kernel_id = "krn_old"
  controller.kernel_state = "on"
  controller.executions.exe_old = { execution_id = "exe_old" }

  controller:connect()
  equal(resynchronized.reason, "supervisor_replaced")
  equal(controller.supervisor_id, "sup_new")
  equal(controller.event_sequence, 1)
  equal(controller.kernel_state, "off")
  equal(controller.kernel_id, nil)
  equal(controller.runtime_id, nil)
  equal(controller.executions, {})
  equal(transport.connected_after, 1)
  controller:close()
  model:detach()
end

local function test_expired_cursor_resynchronizes_but_replayable_cursor_does_not_jump()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "1", "╰──" })
  local model = notebook.attach(buf)
  local health = {
    ok = true,
    status = "ready",
    executions = {},
    clients = {},
    surfaces = {},
    supervisor_id = "sup_test",
    earliest_event_sequence = 10,
    event_sequence = 12,
    kernel = { kernel_id = "krn_snapshot", notebook_id = model.notebook_id, state = "on" },
    runtime = {
      runtime_id = "run_snapshot",
      notebook_id = model.notebook_id,
      discovery_id = "discovery_snapshot",
      kernel_id = "krn_snapshot",
      plugin_catalog = { protocol_version = 1, catalog_version = 1, discovery_id = "discovery_snapshot", plugins = {} },
    },
  }

  local expired_transport = FakeTransport.new(health)
  local expired = controller_module.new({ notebook = model, transport = expired_transport, event_sequence = 2 })
  expired.supervisor_id = "sup_test"
  expired:connect()
  equal(expired.event_sequence, 12)
  equal(expired.kernel_id, "krn_snapshot")
  equal(expired.kernel_state, "on")
  equal(expired.runtime_id, "run_snapshot")
  equal(expired.discovery_id, "discovery_snapshot")
  equal(expired_transport.connected_after, 12)
  expired:close()

  local replay_transport = FakeTransport.new(health)
  local replayable = controller_module.new({ notebook = model, transport = replay_transport, event_sequence = 10 })
  replayable.supervisor_id = "sup_test"
  replayable.kernel_id = "krn_before_replay"
  replayable.kernel_state = "off"
  replayable:connect()
  equal(replayable.event_sequence, 10)
  equal(replayable.kernel_id, "krn_before_replay")
  equal(replayable.kernel_state, "off")
  equal(replay_transport.connected_after, 10)
  replayable:close()
  model:detach()
end

function M.run()
  test_fragmented_sse_parser()
  test_controller_tracks_durable_client_lifecycle()
  test_controller_accepts_authoritative_surface_snapshot()
  test_controller_routes_identity_and_preserves_kernel_truth_on_gap()
  test_invalid_cell_is_rejected_before_transport()
  test_malformed_event_disconnects_without_changing_kernel_truth()
  test_supervisor_replacement_resynchronizes_from_authoritative_snapshot()
  test_expired_cursor_resynchronizes_but_replayable_cursor_does_not_jump()
end

return M
