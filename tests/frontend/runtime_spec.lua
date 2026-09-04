local jusi = require("jusi")

local M = {}

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
  end
end

local function truthy(value, message)
  if not value then
    error(message or "expected a truthy value")
  end
end

local FakeTransport = {}
FakeTransport.__index = FakeTransport

function FakeTransport.new()
  return setmetatable({ transport_id = "trn_runtime", requests = {} }, FakeTransport)
end

function FakeTransport:connect_events(_, callbacks)
  self.callbacks = callbacks
  local connection = {
    closed = false,
    close = function(value)
      value.closed = true
    end,
  }
  callbacks.on_open()
  return connection
end

function FakeTransport:request(method, path, payload, _, callback)
  table.insert(self.requests, { method = method, path = path, payload = payload })
  if path == "/v1/health" then
    callback({
      ok = true,
      status = "ready",
      executions = {},
      clients = {},
      surfaces = {},
      supervisor_id = "sup_runtime",
      earliest_event_sequence = 1,
      event_sequence = 0,
      kernel = nil,
      runtime = vim.NIL,
    }, nil)
  elseif payload.kind == "start_kernel" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime", state = "on" },
      runtime = {
        runtime_id = "run_runtime",
        discovery_id = "discovery_runtime",
        plugin_catalog = { protocol_version = 1, catalog_version = 1, discovery_id = "discovery_runtime", plugins = {} },
      },
    }, nil)
  elseif payload.kind == "execute" then
    callback({ ok = true, execution = { execution_id = "exe_runtime", outcome = "succeeded" } }, nil)
  elseif payload.kind == "interrupt" then
    callback({
      ok = true,
      execution = { execution_id = payload.execution_id, outcome = "running" },
      interrupt = { result = "requested" },
    }, nil)
  elseif payload.kind == "restart_notebook" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime_2", state = "on" },
      runtime = {
        runtime_id = "run_runtime_2",
        notebook_id = payload.next_notebook_id,
        discovery_id = "discovery_runtime_2",
        kernel_id = "krn_runtime_2",
        plugin_catalog = { protocol_version = 1, catalog_version = 1, discovery_id = "discovery_runtime_2", plugins = {} },
      },
      cleanup = { result = "stopped" },
    }, nil)
  elseif payload.kind == "stop_kernel" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime", state = "off" },
      cleanup = { result = "stopped" },
    }, nil)
  elseif payload.kind == "close_client" then
    callback({
      ok = true,
      client_id = payload.client_id,
      cleanup = { result = "stopped" },
    }, nil)
  end
  return {}
end

local function test_client_commands_use_focused_projection_identity()
  local original_notify = vim.notify
  vim.notify = function() end
  local ok, failure = xpcall(function()
    local notebook_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { "╭──", "%%sql main", "select 1", "╰──" })
    vim.api.nvim_win_set_buf(0, notebook_buf)
    local transport = FakeTransport.new()
    local session = jusi.connect({ buf = notebook_buf, transport = transport })
    local cell_id = session.model:cell_at_row(1).id
    local client_buf = vim.api.nvim_create_buf(false, true)
    local terminal_lines = {}
    for _ = 1, 20 do
      table.insert(terminal_lines, "terminal row")
    end
    vim.api.nvim_buf_set_lines(client_buf, 0, -1, false, terminal_lines)
    vim.b[client_buf].jusi_role = "interactive_terminal"
    vim.b[client_buf].jusi_notebook_id = session.model.notebook_id
    vim.b[client_buf].jusi_cell_id = cell_id
    vim.b[client_buf].jusi_client_id = "cli_sql"
    vim.api.nvim_win_set_buf(0, client_buf)
    vim.api.nvim_win_set_cursor(0, { 12, 0 })

    session.controller.kernel_id = "krn_runtime"
    session.controller.kernel_state = "on"
    jusi.execute()
    equal(transport.requests[2].method, "POST")
    equal(transport.requests[2].path, "/v1/kernels/krn_runtime/executions")
    equal(transport.requests[2].payload.cell_id, cell_id)
    equal(transport.requests[2].payload.code, "%%sql main\nselect 1")

    session.controller.executions.exe_active = {
      execution_id = "exe_active",
      cell_id = cell_id,
      outcome = "running",
    }
    jusi.interrupt()
    equal(transport.requests[3].path, "/v1/kernels/krn_runtime/executions/exe_active/interrupt")
    equal(transport.requests[3].payload.execution_id, "exe_active")
    session.controller.executions.exe_active.outcome = "interrupted"

    session.controller.clients.cli_sql = {
      client_id = "cli_sql",
      cell_id = cell_id,
    }
    jusi.close()

    equal(transport.requests[4].method, "DELETE")
    equal(transport.requests[4].path, "/v1/clients/cli_sql")
    equal(transport.requests[4].payload.client_id, "cli_sql")

    session.controller.clients.cli_sql = nil
    session.controller.clients.cli_replaced = {
      client_id = "cli_replaced",
      cell_id = cell_id,
    }
    jusi.execute()
    equal(transport.requests[5].path, "/v1/clients/cli_replaced")
    equal(transport.requests[5].payload.kind, "close_client")
    equal(transport.requests[6].path, "/v1/kernels/krn_runtime/executions")
    equal(transport.requests[6].payload.cell_id, cell_id)
    vim.api.nvim_buf_delete(client_buf, { force = true })
    equal(jusi._destroy_session(notebook_buf), true)
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

local function event(sequence, kind, payload)
  local resource_kind = kind == "service.ready" and "supervisor" or "execution"
  local resource_id = kind == "service.ready" and "sup_runtime" or payload.execution_id
  return {
    protocol_version = 1,
    event_id = "evt_" .. sequence,
    supervisor_id = "sup_runtime",
    sequence = sequence,
    occurred_at = "2026-08-31T12:00:00Z",
    trace_id = "trace_runtime",
    layer = kind == "service.ready" and "service" or "execution",
    operation = kind == "service.ready" and "service_start" or "execute",
    kind = kind,
    resource = { kind = resource_kind, id = resource_id },
    payload = payload,
  }
end

local function test_explicit_command_workflow()
  local notifications = {}
  local original_notify = vim.notify
  vim.notify = function(message, level)
    table.insert(notifications, { message = message, level = level })
  end
  local ok, failure = xpcall(function()
    jusi.setup({ output_height = 5 })
    equal(vim.fn.exists(":JusiConnect"), 2)
    equal(vim.fn.exists(":JusiExecute"), 2)
    equal(vim.fn.exists(":JusiInterrupt"), 2)
    equal(vim.fn.exists(":JusiServiceStart"), 2)
    equal(vim.fn.exists(":JusiServiceStop"), 2)
    equal(vim.fn.exists(":JusiRestart"), 2)
    equal(vim.fn.exists(":JusiClose"), 2)
    equal(vim.fn.exists(":JusiToggleFocus"), 2)

    local notebook_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { "╭──", "1 + 1", "╰──" })
    vim.api.nvim_win_set_buf(0, notebook_buf)
    local notebook_window = vim.api.nvim_get_current_win()
    local transport = FakeTransport.new()
    local session = jusi.connect({ buf = notebook_buf, transport = transport })
    transport.callbacks.on_event(event(1, "service.ready", { supervisor_id = "sup_runtime" }))

    jusi.start_kernel(notebook_buf)
    equal(session.controller.kernel_state, "on")
    jusi.execute(notebook_buf, 1)
    equal(transport.requests[3].payload.code, "1 + 1")
    local cell_id = session.model:cell_at_row(1).id
    transport.callbacks.on_event(event(2, "execution.started", {
      execution_id = "exe_runtime",
      kernel_id = "krn_runtime",
      notebook_id = session.model.notebook_id,
      cell_id = cell_id,
      client_id = "cli_runtime",
      outcome = "running",
      started_at = "2026-08-31T12:00:00Z",
      completed_at = vim.NIL,
    }))
    transport.callbacks.on_event(event(3, "execution.output", {
      execution_id = "exe_runtime",
      client_id = "cli_runtime",
      output_kind = "result",
      media_type = "text/plain",
      data = "2",
    }))
    local output_buf = assert(session.presentation:buffer_for_cell(cell_id))
    equal(vim.api.nvim_get_current_buf(), notebook_buf, "automatic presentation must not steal focus")
    equal(#vim.fn.win_findbuf(output_buf), 1, "execution must reveal its artifact")
    assert(vim.wait(1000, function()
      return vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1] == "2"
    end, 10), "terminal output was not rendered")
    equal(vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1], "2")

    equal(jusi.toggle_focus(notebook_buf, 1), output_buf)
    equal(vim.api.nvim_get_current_buf(), output_buf)
    equal(jusi.toggle_focus(), notebook_buf)
    equal(vim.api.nvim_get_current_win(), notebook_window)
    equal(vim.api.nvim_get_current_buf(), notebook_buf)

    jusi.toggle_focus(notebook_buf, 1)
    vim.api.nvim_win_close(0, false)
    equal(#vim.fn.win_findbuf(output_buf), 0, "native window close must only hide the artifact")
    equal(vim.api.nvim_buf_is_valid(output_buf), true)
    equal(jusi.toggle_focus(notebook_buf, 1), output_buf)
    equal(vim.api.nvim_get_current_buf(), output_buf, "toggle must reopen a hidden artifact")
    equal(jusi.toggle_focus(), notebook_buf)
    equal(jusi.close(notebook_buf, 1), true)
    equal(vim.api.nvim_buf_is_valid(output_buf), false, "JusiClose must destroy ordinary output")

    local old_notebook_id = session.model.notebook_id
    local old_cell_id = cell_id
    local text_before_restart = vim.api.nvim_buf_get_lines(notebook_buf, 0, -1, false)
    jusi.setup({ kernel_name = "python-reloaded" })
    jusi.restart(notebook_buf)
    equal(transport.requests[4].payload.kernel_name, "python-reloaded")
    equal(session.buf, notebook_buf)
    equal(vim.api.nvim_buf_get_lines(notebook_buf, 0, -1, false), text_before_restart)
    truthy(session.model.notebook_id ~= old_notebook_id, "restart must replace frontend notebook identity")
    truthy(session.model:ordered_cells()[1].id ~= old_cell_id, "restart must replace runtime cell identity")
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(session.controller.runtime_id, "run_runtime_2")
    equal(session.controller.kernel_id, "krn_runtime_2")
    equal(session.controller.notebook, session.model)
    jusi.setup({ kernel_name = "python3" })

    jusi.stop_kernel()
    equal(session.controller.kernel_state, "off")
    equal(jusi.disconnect(), true)
    equal(session.controller.transport_state, "disconnected")
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(jusi._sessions[notebook_buf], session)
    local notebook_id = session.model.notebook_id
    jusi.connect({ buf = notebook_buf })
    equal(session.model.notebook_id, notebook_id, "transport reconnection must preserve model identity")
    equal(jusi._destroy_session(notebook_buf), true)
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(jusi._sessions[notebook_buf], nil)
    assert(#notifications >= 3)
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

local function test_vipynb_filetype_and_legacy_connection_guard()
  vim.cmd("runtime ftdetect/jusi.lua")
  equal(vim.filetype.match({ filename = "notebook.vipynb" }), "jusi")

  local notifications = {}
  local original_notify = vim.notify
  vim.notify = function(message, level)
    table.insert(notifications, { message = message, level = level })
  end
  local ok, failure = xpcall(function()
    local legacy_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(legacy_buf, 0, -1, false, { "##", "1 + 1" })
    equal(jusi.connect({ buf = legacy_buf, transport = FakeTransport.new() }), nil)
    equal(jusi._sessions[legacy_buf], nil)
    assert(notifications[1].message:find("legacy 0.x", 1, true))
    vim.api.nvim_buf_delete(legacy_buf, { force = true })
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

function M.run()
  test_explicit_command_workflow()
  test_client_commands_use_focused_projection_identity()
  test_vipynb_filetype_and_legacy_connection_guard()
end

return M
