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
  vim.notify = function() end
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

    vim.api.nvim_chan_send(record.job_id, "opaque-input")
    wait_for(3000, function()
      return terminal_text(record.buf):find("opaque-input", 1, true) ~= nil
    end, "terminal input did not round-trip through the target PTY")

    jusi.close_client(buf, 1)
    wait_for(5000, function()
      return next(session.controller.clients) == nil
        and next(session.controller.surfaces) == nil
        and next(session.interactive.surfaces) == nil
    end, "explicit client close did not retire the terminal surface")
    assert(session.controller.kernel_state == "on")
    assert(not vim.api.nvim_buf_is_valid(record.buf))

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
