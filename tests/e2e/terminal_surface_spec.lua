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
    assert(vim.deep_equal(session.model:history(record.client.cell_id)[1], { "  literal followup  ", "α" }), "followup history lost submitted text")
    wait_for(3000, function() return session.marks.records[record.client.cell_id].state == "followup" end,
      "followup mark stayed busy after delivery")
    assert(session.interactive.surfaces[original_surface] == record, "followup replaced the surface")
    jusi.submit(buf, 1)
    wait_for(5000, function()
      return vim.tbl_contains(notifications, "followup delivered")
    end, "JusiFollowup did not report acceptance")
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "select * from SUFFIX" })
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
      return terminal_text(record.buf):find("target:h", 1, true) ~= nil
    end, "single terminal key did not immediately round-trip through the target PTY")

    local first_client_id = record.client.client_id
    local first_surface_id = record.surface.surface_id
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
    assert(not vim.api.nvim_buf_is_valid(record.buf))

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
