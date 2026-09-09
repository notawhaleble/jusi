local jusi = require("jusi")

local M = {}

local function wait_for(timeout, predicate, message)
  assert(vim.wait(timeout, predicate, 10), message)
end

local function terminal_text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end

function M.run()
  local original_notify = vim.notify
  local original_pythonpath = vim.env.PYTHONPATH
  local notifications = {}
  vim.notify = function(message)
    table.insert(notifications, tostring(message))
  end
  vim.env.PYTHONPATH = vim.fn.getcwd() .. "/tests/fixtures/terminal_plugin"
    .. (original_pythonpath and (":" .. original_pythonpath) or "")
  jusi.setup({
    terminal_bridge_command = { ".venv/bin/python", "-m", "jusi", "terminal-bridge" },
  })
  local buf
  local service
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
      "╭──", "%%terminal_fixture", "manual fixture", "╰──",
    })
    vim.api.nvim_win_set_buf(0, buf)
    service = jusi.start_service({
      buf = buf,
      command = { ".venv/bin/python", "-m", "jusi", "serve" },
      timeout_ms = 8000,
    })
    wait_for(8000, function()
      local session = jusi._sessions[buf]
      return session and session.controller.transport_state == "connected"
    end, "terminal fixture service did not connect")
    local session = jusi._sessions[buf]
    jusi.start_kernel(buf)
    wait_for(10000, function()
      return session.controller.kernel_state == "on"
    end, "terminal fixture kernel did not start")

    jusi.execute(buf, 1)
    wait_for(10000, function()
      return next(session.interactive.surfaces) ~= nil
    end, "interactive terminal surface was not projected")
    local _, record = next(session.interactive.surfaces)
    wait_for(3000, function()
      return terminal_text(record.buf):find("initial=", 1, true) ~= nil
    end, "target application did not report its first geometry")
    local expected_geometry = string.format(
      "initial=%dx%d",
      vim.api.nvim_win_get_width(record.window),
      vim.api.nvim_win_get_height(record.window)
    )
    assert(terminal_text(record.buf):find(expected_geometry, 1, true), terminal_text(record.buf))
    assert(vim.api.nvim_get_current_buf() == buf, "execution artifact stole notebook focus")

    wait_for(3000, function()
      local mark = session.marks.records[record.client.cell_id]
      return mark and mark.state == "followup"
    end, "durable client did not receive its followup mark")
    assert(vim.deep_equal(session.model:history(record.client.cell_id), { { "manual fixture" } }), "initial handoff was not captured")
    local original_client = record.client.client_id
    local original_surface = record.surface.surface_id
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "  literal followup  ", "α" })
    local response, followup_failure
    session.controller:followup(session.model:cell_at_row(1).id, function(result, err)
      response, followup_failure = result, err
    end)
    wait_for(5000, function() return response ~= nil or followup_failure ~= nil end, "followup did not finish")
    assert(not followup_failure, vim.inspect(followup_failure))
    assert(response.result.body == "  literal followup  \nα" and response.result.count == 1)
    assert(response.client.client_id == original_client)
    wait_for(3000, function()
      local text = terminal_text(record.buf)
      return text:find("followup 1:", 1, true) and text:find("literal followup", 1, true) and text:find("α", 1, true)
    end, "plugin did not visibly display its followup body")
    assert(vim.deep_equal(session.model:history(record.client.cell_id)[1], { "  literal followup  ", "α" }), "followup history lost submitted text")
    wait_for(3000, function() return session.marks.records[record.client.cell_id].state == "followup" end,
      "followup mark stayed busy after delivery")
    assert(session.interactive.surfaces[original_surface] == record, "followup replaced the surface")
    jusi.submit(buf, 1)
    wait_for(5000, function()
      return terminal_text(record.buf):find("followup 2:", 1, true) ~= nil
    end, "followup was not displayed by the plugin")
    -- Exact concurrent plugin interruption keeps this client and terminal alive.
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "fixture:wait" })
    local interrupted_response, interrupt_failure
    session.controller:followup(record.client.cell_id, function(result, err)
      interrupted_response, interrupt_failure = result, err
    end)
    wait_for(3000, function() return terminal_text(record.buf):find("interruptible fixture operation", 1, true) end,
      "fixture did not enter its interruptible operation")
    wait_for(3000, function() return next(session.controller.client_operations) ~= nil end, "no active plugin identity")
    -- The target application can copy while ordinary worker work is blocked.
    vim.fn.setreg('"', "before application copy")
    vim.api.nvim_chan_send(record.job_id, "action:copy\r")
    wait_for(3000, function() return vim.fn.getreg('"') == "  literal followup  \nα" end,
      "application copy blocked behind followup")
    wait_for(3000, function() return terminal_text(record.buf):find("action:copy delivered", 1, true) end,
      "application did not receive copy acknowledgment")
    local old_operation = next(session.controller.client_operations)
    local snapshot
    session.controller.transport:request("GET", "/v1/health", nil, {}, function(result) snapshot = result end)
    wait_for(2000, function() return snapshot ~= nil end, "health blocked behind plugin work")
    assert(snapshot.client_operations[1].operation_id == old_operation)
    jusi.interrupt(buf, 1)
    wait_for(3000, function() return interrupted_response or interrupt_failure end, "plugin interrupt blocked")
    assert(not interrupt_failure, vim.inspect(interrupt_failure))
    assert(interrupted_response.operation.outcome == "cancelled")
    assert(session.controller.clients[original_client] and session.interactive.surfaces[original_surface] == record)
    local stale_failure
    session.controller:interrupt_client(original_client, old_operation, function(_, err) stale_failure = err end)
    wait_for(3000, function() return stale_failure ~= nil end, "stale interrupt was not rejected")
    assert(stale_failure.reason == "conflict")

    -- Exports arrive in an HTTP body and mutate only the local destination.
    local export_text = "  literal followup  \nα"
    vim.fn.setreg("a", "before")
    jusi.editor_action("copy", { buf = buf, row = 1, register = "a" })
    wait_for(3000, function() return vim.fn.getreg("a") == export_text end, "HTTP copy did not arrive")
    local rejected
    session.controller:editor_action(record.client.cell_id, "copy", { scope = "missing" }, function(_, err) rejected = err end)
    wait_for(3000, function() return rejected ~= nil end, "missing selection did not fail")
    assert(rejected.reason == "plugin_error" and session.controller.clients[original_client])
    assert(vim.fn.getreg("a") == export_text)
    jusi.editor_action("open", { buf = buf, row = 1 })
    wait_for(3000, function() return vim.b.jusi_export_name == "selection.txt" end, "HTTP open did not arrive")
    local exported_buf = vim.api.nvim_get_current_buf()
    assert(table.concat(vim.api.nvim_buf_get_lines(exported_buf, 0, -1, false), "\n") == export_text)
    assert(vim.bo[exported_buf].modifiable and vim.bo[exported_buf].modified)
    assert(vim.fn.filereadable(vim.api.nvim_buf_get_name(exported_buf)) == 0)
    vim.api.nvim_win_close(0, true)
    vim.api.nvim_set_current_buf(buf)
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "select * from SUFFIX" })
    vim.api.nvim_win_set_cursor(0, { 2, #"select * from " })
    _G.jusi_e2e_plugin_ready = function()
      wait_for(5000, function() return require("jusi.completion")._active[buf] ~= nil end, "plugin completion reply did not arrive")
    end
    _G.jusi_e2e_plugin_complete = function()
      assert(vim.fn.pumvisible() == 1, "plugin empty-prefix menu did not appear")
      assert(vim.api.nvim_buf_get_lines(buf, 1, 2, false)[1] == "select * from public.SUFFIX")
      vim.api.nvim_select_popupmenu_item(1, true, false, {})
    end
    vim.v.errmsg = ""
    vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(
      "i<Tab><Cmd>lua jusi_e2e_plugin_ready()<CR><Cmd>lua jusi_e2e_plugin_complete()<CR><C-y><Esc>", true, false, true), "xt", false)
    assert(vim.v.errmsg == "", vim.v.errmsg)
    _G.jusi_e2e_plugin_complete, _G.jusi_e2e_plugin_ready = nil, nil
    assert(vim.api.nvim_buf_get_lines(buf, 1, 2, false)[1] == "select * from private.SUFFIX")
    assert(session.interactive.surfaces[original_surface] == record, "completion replaced the client surface")
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "  literal followup  ", "α" })
    -- Restore executable source for the subsequent close/re-execute checks.
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "%%terminal_fixture", "manual fixture" })

    vim.api.nvim_win_close(record.window, false)
    assert(vim.api.nvim_buf_is_valid(record.buf), "native window close destroyed the artifact")
    assert(session.controller.clients[record.client.client_id] ~= nil)
    assert(jusi.toggle_focus(buf, 1) == record.buf, "toggle did not reopen the hidden artifact")
    assert(vim.api.nvim_get_current_buf() == record.buf)
    assert(jusi.toggle_focus() == buf, "toggle did not return to the source cell")
    assert(vim.api.nvim_get_current_buf() == buf)

    vim.api.nvim_chan_send(record.job_id, "h")
    wait_for(3000, function()
      return terminal_text(record.buf):find("> h", 1, true) ~= nil
    end, "single terminal key did not immediately round-trip through the target PTY")

    vim.api.nvim_chan_send(record.job_id, "ello\r")
    wait_for(3000, function() return terminal_text(record.buf):find("input: hello", 1, true) ~= nil end,
      "terminal typing did not produce a readable complete line")
    assert(not vim.tbl_contains(notifications, "followup delivered"))
    -- Open requests originate solely in the terminal application/helper.
    local application_exports = {}
    for _, action in ipairs({ "open", "file" }) do
      vim.api.nvim_chan_send(record.job_id, "action:" .. action .. "\r")
      local expected_name = action == "open" and "application.txt" or "application-file.txt"
      wait_for(4000, function() return vim.b.jusi_export_name == expected_name end, "application open did not arrive")
      local exported = vim.api.nvim_get_current_buf()
      table.insert(application_exports, exported)
      assert(table.concat(vim.api.nvim_buf_get_lines(exported, 0, -1, false), "\n") == export_text)
      wait_for(3000, function() return terminal_text(record.buf):find("action:" .. action .. " delivered", 1, true) end,
        "application open was not acknowledged")
      vim.api.nvim_win_close(0, true)
      vim.api.nvim_set_current_buf(buf)
    end
    local first_client_id = record.client.client_id
    local first_surface_id = record.surface.surface_id
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "fixture:wait" })
    local close_response, close_failure
    session.controller:followup(record.client.cell_id, function(result, err) close_response, close_failure = result, err end)
    wait_for(3000, function() return next(session.controller.client_operations) ~= nil end, "close test has no active work")
    vim.fn.jobstop(record.job_id)
    wait_for(3000, function()
      return vim.fn.jobwait({ record.job_id }, 0)[1] ~= -1
    end, "frontend terminal bridge did not stop")
    assert(session.controller.clients[first_client_id] ~= nil)
    assert(session.controller.surfaces[first_surface_id] ~= nil)
    assert(session.controller.kernel_state == "on")

    jusi.close(buf, 1)
    wait_for(5000, function()
      return next(session.controller.clients) == nil
        and next(session.controller.surfaces) == nil
        and next(session.interactive.surfaces) == nil
    end, "explicit client close did not retire the terminal surface")
    assert(session.controller.kernel_state == "on")
    wait_for(3000, function() return close_response or close_failure end, "close left followup hanging")
    assert(close_response and close_response.operation.outcome == "cancelled", vim.inspect(close_failure))
    assert(not vim.api.nvim_buf_is_valid(record.buf))
    for _, exported in ipairs(application_exports) do
      assert(vim.api.nvim_buf_is_valid(exported), "client close destroyed application export")
      vim.api.nvim_buf_delete(exported, { force = true })
    end
    assert(vim.api.nvim_buf_is_valid(exported_buf), "source close destroyed independent exported buffer")
    vim.api.nvim_buf_delete(exported_buf, { force = true })
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "%%terminal_fixture", "manual fixture" })

    jusi.execute(buf, 1)
    wait_for(10000, function()
      return next(session.interactive.surfaces) ~= nil
    end, "replacement interactive terminal surface was not projected")
    local _, replacement = next(session.interactive.surfaces)
    wait_for(3000, function()
      return terminal_text(replacement.buf):find("initial=", 1, true) ~= nil
    end, "replacement target application did not start")
    vim.api.nvim_set_current_win(replacement.window)
    vim.api.nvim_chan_send(replacement.job_id, "\x03")
    wait_for(5000, function()
      return next(session.controller.clients) == nil
        and next(session.controller.surfaces) == nil
        and next(session.interactive.surfaces) == nil
    end, "target application exit did not retire its client and surface")
    assert(session.controller.kernel_state == "on")
    assert(not vim.api.nvim_buf_is_valid(replacement.buf))
    assert(vim.iter(notifications):any(function(message)
      return message:find("client/run_terminal_surface/channel_closed", 1, true) ~= nil
    end), vim.inspect(notifications))

    assert(vim.api.nvim_get_current_buf() == buf,
      "focused terminal exit did not return to the notebook buffer")
    vim.api.nvim_win_set_cursor(0, { 2, 0 })
    assert(session.model:cell_at_row(1) ~= nil,
      "focused terminal exit invalidated the notebook cell model")
    jusi.execute()
    wait_for(10000, function()
      return next(session.interactive.surfaces) ~= nil
    end, "notebook execution after focused target exit did not create a new client")
    local _, final_record = next(session.interactive.surfaces)
    local retired_cell = session.model:cell_at_row(1).id
    -- Damaging the opener must fully close its durable client and terminal worker.
    vim.api.nvim_buf_set_text(buf, 0, 0, 0, 0, { "x" })
    assert(session.model:cell_by_id(retired_cell) == nil)
    wait_for(5000, function()
      return next(session.controller.clients) == nil
        and next(session.controller.surfaces) == nil
        and next(session.interactive.surfaces) == nil
    end, "opener retirement did not close the client and terminal surface")
    assert(not vim.api.nvim_buf_is_valid(final_record.buf))
    assert(session.controller.kernel_state == "on")

    jusi.stop_service(buf)
    wait_for(8000, function()
      return service.state == "stopped" and jusi._sessions[buf] == nil
    end, "terminal fixture service did not stop")
  end, debug.traceback)
  if buf and jusi._sessions[buf] then
    jusi._destroy_session(buf)
  elseif service and service.state ~= "stopped" then
    service:stop()
  end
  vim.env.PYTHONPATH = original_pythonpath
  vim.notify = original_notify
  if not ok then
    error(failure .. "\nservice stderr:\n" .. (service and service.stderr or ""))
  end
  print("Interactive terminal surface passed")
end

return M
