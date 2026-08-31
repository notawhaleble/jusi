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

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "owned_restart_probe = 1" })
    local before_probe_sequence = session.controller.event_sequence
    jusi.execute(buf, 1)
    wait_for(8000, function()
      return session.controller.event_sequence >= before_probe_sequence + 4
    end, "restart probe assignment did not complete")

    local old_runtime_id = session.controller.runtime_id
    local old_notebook_id = session.model.notebook_id
    local old_cell_id = session.model:cell_at_row(1).id
    local undo_sequence = vim.fn.undotree().seq_cur
    jusi.restart(buf)
    wait_for(12000, function()
      return session.controller.runtime_id ~= old_runtime_id and session.model.notebook_id ~= old_notebook_id
    end, "JusiRestart did not replace the complete frontend/backend runtime")
    assert(session.model:cell_at_row(1).id ~= old_cell_id)
    assert(vim.api.nvim_buf_get_lines(buf, 1, 2, false)[1] == "owned_restart_probe = 1")
    assert(vim.fn.undotree().seq_cur == undo_sequence)
    assert(not vim.api.nvim_buf_is_valid(output_buf))

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "int('owned_restart_probe' in globals())" })
    local restarted_cell_id = session.model:cell_at_row(1).id
    jusi.execute(buf, 1)
    wait_for(8000, function()
      return session.presentation:buffer_for_cell(restarted_cell_id) ~= nil
    end, "post-JusiRestart execution produced no output surface")
    local restarted_output = session.presentation:buffer_for_cell(restarted_cell_id)
    wait_for(1000, function()
      return vim.api.nvim_buf_get_lines(restarted_output, 0, 1, false)[1] == "0"
    end, "JusiRestart retained a global from the old kernel")

    jusi.stop_service(buf)
    wait_for(8000, function()
      return service.state == "stopped" and session.service == nil and jusi._sessions[buf] == nil
    end, "owned local service did not stop")
    assert(session.controller.kernel_state == "off")

    local first_supervisor_id = service.supervisor_id
    service = jusi.start_service({
      buf = buf,
      command = { ".venv/bin/python", "-m", "jusi", "serve" },
      timeout_ms = 8000,
    })
    wait_for(8000, function()
      local replacement = jusi._sessions[buf]
      return replacement and replacement.controller.transport_state == "connected"
    end, "owned local service did not restart after confirmed stop")
    local replacement = jusi._sessions[buf]
    assert(replacement.service == service)
    assert(service.supervisor_id ~= first_supervisor_id)
    jusi.stop_service(buf)
    wait_for(8000, function()
      return service.state == "stopped" and jusi._sessions[buf] == nil
    end, "replacement owned local service did not stop")
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
