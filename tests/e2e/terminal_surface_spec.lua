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

    jusi.close_client(buf, 1, first_client_id)
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
    jusi.close_client(buf, 1, final_record.client.client_id)
    wait_for(5000, function()
      return next(session.controller.clients) == nil
        and next(session.controller.surfaces) == nil
        and next(session.interactive.surfaces) == nil
    end, "final client cleanup did not converge")

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
