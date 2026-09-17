local jusi = require("jusi")
local completion = require("jusi.completion")
local M = {}
local function wait_for(predicate, message) assert(vim.wait(8000, predicate, 10), message) end
function M.run()
  local old_notify, notifications = vim.notify, {}
  vim.notify = function(message) notifications[#notifications + 1] = message end
  local buf, service, session
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "jusi_completion_global = 42", "╰──" })
    vim.api.nvim_set_current_buf(buf)
    vim.bo[buf].completeopt = "menuone"
    service = jusi.start_service({ buf = buf, command = { ".venv/bin/python", "-m", "jusi", "serve" } })
    wait_for(function()
      session = jusi._sessions[buf]
      return session and session.controller.transport_state == "connected"
    end, "completion service did not connect")
    jusi.start_kernel(buf)
    wait_for(function() return session.controller.kernel_state == "on" end, "completion kernel did not start")
    local completed, starts = false, 0
    session.controller.on_event = function(event)
      if event.kind == "execution.started" then starts = starts + 1 end
      if event.kind == "execution.completed" then completed = true end
    end
    vim.api.nvim_win_set_cursor(0, { 2, 0 })
    vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes('i<C-Y><Esc>', true, false, true), 'xt', false)
    wait_for(function() return completed end, "scope setup did not complete")
    wait_for(function() return vim.api.nvim_buf_get_lines(buf, 1, 2, false)[1] == '' end,
      'Ctrl-Y did not clear the executed body')
    for _, case in ipairs({
      { prefix = "jusi_completion_g", choice = "jusi_completion_global", expected = "jusi_completion_globalSUFFIX" },
      { prefix = "from time import s", choice = "sleep", expected = "from time import sleepSUFFIX" },
    }) do
      vim.api.nvim_buf_set_lines(buf, 1, 2, false, { case.prefix .. "SUFFIX" })
      vim.api.nvim_win_set_cursor(0, { 2, #case.prefix })
      local ready, ready_error, selected_ok, selected_error
      _G.jusi_e2e_ready = function()
        ready, ready_error = pcall(wait_for, function() return completion._active[buf] ~= nil end,
          "completion reply did not arrive: " .. vim.inspect(notifications))
      end
      _G.jusi_e2e_complete = function()
        selected_ok, selected_error = xpcall(function()
        assert(vim.fn.pumvisible() == 1, "native menu did not appear")
        local menu = assert(completion._active[buf])
        local selected
        for index, item in ipairs(menu.items) do if item.text == case.choice then selected = index end end
        assert(selected, vim.inspect(menu.items))
        vim.api.nvim_select_popupmenu_item(selected - 1, true, false, {})
        assert(vim.api.nvim_buf_get_lines(buf, 1, 2, false)[1] == case.expected)
        assert(vim.api.nvim_win_get_cursor(0)[2] == #case.expected - #"SUFFIX")
        end, debug.traceback)
      end
      vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(
        "i<Tab><Cmd>lua jusi_e2e_ready()<CR><Cmd>lua jusi_e2e_complete()<CR><C-y><Esc>", true, false, true), "xt", false)
      assert(ready, ready_error)
      assert(selected_ok, selected_error)
      assert(vim.api.nvim_buf_get_lines(buf, 1, 2, false)[1] == case.expected)
      _G.jusi_e2e_complete, _G.jusi_e2e_ready = nil, nil
    end
    assert(starts == 1, "completion created a kernel execution")
    local palette = require("jusi.palette")
    assert(session.controller.palette.vd, "startup did not publish bundled magic")
    local label
    for _, item in ipairs(palette.notebooks()) do if item.buf == buf then label = item.label end end
    completed = false
    palette.command({ fargs = { label }, range = 0, bang = false })
    vim.cmd.stopinsert()
    local row = vim.api.nvim_win_get_cursor(0)[1] - 1
    vim.api.nvim_buf_set_lines(buf, row, row + 1, false, { "21 * 2" })
    jusi.submit(buf, row)
    wait_for(function() return completed end, "palette cell did not execute")
    assert(starts == 2)
    local keys = vim.api.nvim_replace_termcodes("<C-\\><C-\\>", true, false, true)
    vim.api.nvim_feedkeys(keys, "xt", false)
    assert(vim.b.jusi_role == "output", "focus mapping did not reach output")
    vim.api.nvim_feedkeys(keys, "xt", false)
    assert(vim.api.nvim_get_current_buf() == buf, "focus mapping did not return to notebook")
    jusi.stop_service(buf)
    wait_for(function() return service.state == "stopped" end, "completion cleanup failed")
  end, debug.traceback)
  _G.jusi_e2e_complete = nil
  if buf and jusi._sessions[buf] then jusi._destroy_session(buf)
  elseif service and service.state ~= "stopped" then service:stop() end
  vim.notify = old_notify
  if not ok then error(failure .. "\nnotifications: " .. vim.inspect(notifications)) end
  print("Kernel completion menu passed")
end
return M
