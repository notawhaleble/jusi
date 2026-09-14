local function wait(predicate, message)
  assert(vim.wait(20000, predicate, 20), message)
end
local function text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end
assert(vim.fn.has("nvim-0.11") == 1, "Neovim 0.11+ required")
assert(vim.fn.exists(":JusiStart") == 2, "installed plugin did not load automatically")
vim.cmd("edit installed.vipynb")
local buf = vim.api.nvim_get_current_buf()
assert(vim.bo[buf].filetype == "jusi", "installed filetype detection is missing")
local jusi = require("jusi")
local session
local ok, failure = xpcall(function()
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──",
    "import os, sys, jusi; assert sys.prefix == os.environ['VIRTUAL_ENV']; assert jusi.__file__.startswith(sys.prefix)",
    "40 + 2", "╰──" })
  vim.cmd("JusiStart local")
  wait(function()
    session = jusi._sessions[buf]
    return session and session.controller.kernel_state == "on" and not jusi._starts[buf]
  end, "installed target did not start")
  local service = session.service
  local cell = session.model:cell_at_row(1)
  vim.cmd("JusiExecute")
  wait(function()
    local output = session.presentation:buffer_for_cell(cell.id)
    return output and text(output):find("42", 1, true)
  end, "installed kernel did not execute")
  if vim.env.JUSI_SMOKE_VD == "1" then
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "%%vd", "[{'value': 'installed α'}]" })
    vim.cmd("JusiExecute")
    local record
    wait(function()
      _, record = next(session.interactive.surfaces)
      return record and record.buf and text(record.buf):find("installed α", 1, true)
    end, "installed VisiData did not render")
    vim.fn.chansend(record.job_id, "zY")
    wait(function() return vim.fn.getreg('"') == "installed α" end, "installed VisiData copy failed")
    vim.fn.chansend(record.job_id, "\15")
    wait(function() return vim.api.nvim_buf_get_name(0):find("visidata.txt", 1, true) end, "installed VisiData open failed")
    assert(text(vim.api.nvim_get_current_buf()) == "installed α")
    vim.api.nvim_set_current_win(vim.fn.win_findbuf(buf)[1])
  end
  vim.cmd("JusiStop")
  wait(function() return service.state == "stopped" and not jusi._sessions[buf] end, "installed target did not stop")
end, debug.traceback)
if not ok then
  if session then jusi._destroy_session(buf) end
  error(failure)
end
print("Installed " .. (vim.env.JUSI_SMOKE_VD == "1" and "vd copy/open" or "base execution") .. " passed")
vim.cmd("qa!")
