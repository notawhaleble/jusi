local jusi = require("jusi")

local M = {}

local function wait_for(timeout, predicate, message)
  assert(vim.wait(timeout, predicate, 10), message)
end

function M.run()
  local original_notify = vim.notify
  vim.notify = function() end
  local buf
  local service
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "1 + 1", "╰──" })
    vim.api.nvim_win_set_buf(0, buf)
    service = jusi.start_service({
      buf = buf,
      command = { ".venv/bin/python", "-m", "jusi", "serve" },
      timeout_ms = 8000,
    })
    wait_for(8000, function()
      local session = jusi._sessions[buf]
      return session and session.controller.transport_state == "connected"
    end, "owned local service did not connect")
    local session = jusi._sessions[buf]
    assert(session.service == service)
    assert(service.state == "running")

    jusi.start_kernel(buf)
    wait_for(8000, function()
      return session.controller.kernel_state == "on"
    end, "owned local kernel did not start")
    jusi.execute(buf, 1)
    local cell_id = session.model:cell_at_row(1).id
    wait_for(8000, function()
      return session.presentation:buffer_for_cell(cell_id) ~= nil
    end, "owned local execution produced no output surface")
    local output_buf = session.presentation:buffer_for_cell(cell_id)
    wait_for(1000, function()
      return vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1] == "2"
    end, "owned local result was not rendered")

    jusi.stop_service(buf)
    wait_for(8000, function()
      return service.state == "stopped" and session.service == nil
    end, "owned local service did not stop")
    assert(session.controller.kernel_state == "off")
  end, debug.traceback)
  if buf and jusi._sessions[buf] then
    jusi._destroy_session(buf)
  elseif service and service.state ~= "stopped" then
    service:stop()
  end
  vim.notify = original_notify
  if not ok then
    error(failure .. "\nservice stderr:\n" .. (service and service.stderr or ""))
  end
  print("Owned local service runtime passed")
end

return M
