local jusi = require("jusi")

local M = {}

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
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
  if payload.kind == "start_kernel" then
    callback({ ok = true, kernel = { kernel_id = "krn_runtime", state = "on" } }, nil)
  elseif payload.kind == "execute" then
    callback({ ok = true, execution = { execution_id = "exe_runtime", outcome = "succeeded" } }, nil)
  elseif payload.kind == "stop_kernel" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime", state = "off" },
      cleanup = { result = "stopped" },
    }, nil)
  end
  return {}
end

local function event(sequence, kind, payload)
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
    resource = { kind = kind == "service.ready" and "supervisor" or "execution", id = "resource_runtime" },
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

    local notebook_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { "╭──", "1 + 1", "╰──" })
    vim.api.nvim_win_set_buf(0, notebook_buf)
    local transport = FakeTransport.new()
    local session = jusi.connect({ buf = notebook_buf, transport = transport })
    transport.callbacks.on_event(event(1, "service.ready", { supervisor_id = "sup_runtime" }))

    jusi.start_kernel(notebook_buf)
    equal(session.controller.kernel_state, "on")
    jusi.execute(notebook_buf, 1)
    equal(transport.requests[2].payload.code, "1 + 1")
    local cell_id = session.model:cell_at_row(1).id
    transport.callbacks.on_event(event(2, "execution.started", {
      execution_id = "exe_runtime",
      kernel_id = "krn_runtime",
      notebook_id = session.model.notebook_id,
      cell_id = cell_id,
      client_id = "cli_runtime",
      outcome = "running",
    }))
    transport.callbacks.on_event(event(3, "execution.output", {
      execution_id = "exe_runtime",
      client_id = "cli_runtime",
      output_kind = "result",
      media_type = "text/plain",
      data = "2",
    }))
    local output_buf = assert(jusi.open_output(notebook_buf, 1))
    equal(vim.api.nvim_get_current_buf(), output_buf)
    assert(vim.wait(1000, function()
      return vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1] == "2"
    end, 10), "terminal output was not rendered")
    equal(vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1], "2")

    jusi.stop_kernel()
    equal(session.controller.kernel_state, "off")
    equal(jusi.disconnect(), true)
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(jusi._sessions[notebook_buf], nil)
    assert(#notifications >= 3)
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

function M.run()
  test_explicit_command_workflow()
end

return M
