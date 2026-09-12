local jusi = require("jusi")
local M = {}
local function wait_for(timeout, predicate, message)
  assert(vim.wait(timeout, predicate, 10), message)
end
local function text(buf)
  if not vim.api.nvim_buf_is_valid(buf) then return "<closed>" end
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end
function M.run()
  local original_home = vim.env.HOME
  local test_home = vim.fn.tempname()
  vim.fn.mkdir(test_home, "p")
  vim.env.HOME = test_home
  local original_notify = vim.notify
  vim.notify = function() end
  jusi.setup({ terminal_bridge_command = { ".venv/bin/python", "-m", "jusi", "terminal-bridge" } })
  local buf, service, exported, session
  local failures = {}
  local expected = "literal α value" .. string.rep("x", 1200000)
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
      "╭──", "%%vd", "globals().setdefault('vd_rows', [{'value': 'literal α value' + 'x' * 1200000}])", "╰──",
    })
    vim.api.nvim_win_set_buf(0, buf)
    service = jusi.start_service({ buf = buf, command = { ".venv/bin/python", "-m", "jusi", "serve" }, timeout_ms = 8000 })
    wait_for(8000, function() return jusi._sessions[buf] and jusi._sessions[buf].controller.transport_state == "connected" end, "vd service did not connect")
    session = jusi._sessions[buf]
    local original_failure = session.controller.on_failure
    session.controller.on_failure = function(failure)
      table.insert(failures, failure)
      if original_failure then original_failure(failure) end
    end
    jusi.start_kernel(buf)
    wait_for(10000, function() return session.controller.kernel_state == "on" end, "vd kernel did not start")
    vim.api.nvim_buf_set_lines(buf, 2, 3, false, { "object()" })
    jusi.execute(buf, 1)
    wait_for(5000, function()
      for _, execution in pairs(session.controller.executions) do
        if execution.outcome == "failed" then return true end
      end
      return false
    end, "unsupported vd value did not fail visibly")
    assert(session.controller.kernel_state == "on" and next(session.controller.clients) == nil)
    vim.api.nvim_buf_set_lines(buf, 2, 3, false, { "globals().setdefault('vd_rows', [{'value': 'literal α value' + 'x' * 1200000}])" })
    jusi.execute(buf, 1)
    wait_for(10000, function() return next(session.interactive.surfaces) ~= nil end, "vd surface did not open: " .. vim.inspect(session.controller.failures))
    local _, record = next(session.interactive.surfaces)
    assert(vim.wait(8000, function() return text(record.buf):find("literal α value", 1, true) ~= nil end, 10), "vd did not show kernel value: " .. text(record.buf) .. vim.inspect(failures))
    assert(not text(record.buf):find("FileExistsError", 1, true), "VisiData state directory initialization raced")
    local source_client = record.client.client_id
    assert(record.client.plugin_id == "jusi_vd")
    assert(not vim.tbl_contains(record.client.capabilities, "followup"), "vd must not advertise legacy no-op followup")
    vim.fn.setreg('"', 'before-vd-copy')
    vim.fn.chansend(record.job_id, "zY")
    wait_for(5000, function() return vim.fn.getreg('"') == expected end, "vd zY did not deliver to unnamed register: " .. text(record.buf))
    assert(vim.api.nvim_get_current_buf() == buf, "copy moved editor focus")
    vim.fn.chansend(record.job_id, "\15")
    assert(vim.wait(5000, function() return vim.api.nvim_buf_get_name(0):find("visidata.txt", 1, true) ~= nil end, 10), "vd Ctrl-O did not open a split: " .. text(record.buf) .. vim.inspect(failures))
    exported = vim.api.nvim_get_current_buf()
    assert(text(exported) == expected)
    assert(not vim.bo[exported].endofline)
    assert(session.controller.clients[source_client] ~= nil)
    vim.api.nvim_set_current_win(record.window)
    jusi.close()
    wait_for(5000, function() return next(session.controller.clients) == nil and next(session.interactive.surfaces) == nil end, "vd close did not retire client")
    assert(vim.api.nvim_buf_is_valid(exported) and text(exported) == expected)
    assert(session.controller.kernel_state == "on")
    -- Reuse the original kernel namespace after full client cleanup.
    local notebook_win = vim.fn.win_findbuf(buf)[1]
    vim.api.nvim_set_current_win(notebook_win)
    vim.api.nvim_buf_set_lines(buf, 2, 3, false, { "__import__('pandas').DataFrame(vd_rows, index=['row-one'])" })
    jusi.execute(buf, 1)
    wait_for(10000, function() return next(session.interactive.surfaces) ~= nil end, "vd re-execution lost kernel namespace")
    local _, replacement = next(session.interactive.surfaces)
    assert(replacement.client.client_id ~= source_client)
    wait_for(8000, function() return text(replacement.buf):find("literal α value", 1, true) ~= nil end, "replacement vd did not render")
    jusi.stop_service(buf)
    wait_for(8000, function() return service.state == "stopped" and jusi._sessions[buf] == nil end, "vd service did not stop")
    assert(vim.api.nvim_buf_is_valid(exported))
  end, debug.traceback)
  if buf and jusi._sessions[buf] then jusi._destroy_session(buf)
  elseif service and service.state ~= "stopped" then service:stop() end
  if exported and vim.api.nvim_buf_is_valid(exported) then vim.api.nvim_buf_delete(exported, { force = true }) end
  vim.env.HOME = original_home
  vim.fn.delete(test_home, "rf")
  vim.notify = original_notify
  if not ok then error(failure .. "\nfailures=" .. vim.inspect(failures) .. "\nservice stderr:\n" .. (service and service.stderr or "")) end
  print("Bundled VisiData copy/open passed")
end
return M
