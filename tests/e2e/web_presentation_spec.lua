local M = {}
local jusi = require('jusi')
local function wait_for(predicate, message) assert(vim.wait(10000, predicate, 10), message) end
function M.run()
  local buf, service, url, replaced_buf, output_win
  vim.api.nvim_create_user_command('JusiWebBuffer', function(command)
    url = command.args
    replaced_buf, output_win = vim.api.nvim_get_current_buf(), vim.api.nvim_get_current_win()
    vim.api.nvim_set_current_buf(vim.api.nvim_create_buf(true, true))
  end, { nargs = '?' })
  local ok, err = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_set_current_buf(buf)
    local origin = vim.api.nvim_get_current_win()
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
      '╭──', 'from IPython.display import display', "print('Building the font cache')",
      "display({'text/plain': 'Figure', 'image/svg+xml': '<svg xmlns=\"http://www.w3.org/2000/svg\"><text>' + 'plot ' * 10000 + '</text></svg>'}, raw=True)", '╰──',
    })
    service = jusi.start_service({ buf = buf, command = { '.venv/bin/python', '-m', 'jusi', 'serve' }, timeout_ms = 8000 })
    wait_for(function() return jusi._sessions[buf] and jusi._sessions[buf].controller.transport_state == 'connected' end, 'connect')
    local session = jusi._sessions[buf]
    jusi.start_kernel(buf)
    wait_for(function() return session.controller.kernel_state == 'on' end, 'start kernel')
    local done, response, failure
    jusi.execute(buf, 1, function(r, f) done, response, failure = true, r, f end)
    wait_for(function() return done and url ~= nil end, 'rich bundle through HTTP/SSE')
    assert(not failure and response.execution.outcome == 'succeeded', vim.inspect(failure))
    assert(vim.api.nvim_get_current_win() == origin)
    local cell = session.model:cell_at_row(1)
    local output = assert(session.presentation:buffer_for_cell(cell.id))
    assert(vim.b[output].jusi_cell_id == cell.id)
    assert(vim.b[replaced_buf].jusi_role == 'output', 'rich output did not replace the preceding terminal')
    assert(vim.api.nvim_win_get_buf(output_win) == output)
    assert(#vim.fn.win_findbuf(replaced_buf) == 0 and vim.api.nvim_buf_is_valid(replaced_buf))
    local path = vim.uri_to_fname(url)
    assert(vim.fn.filereadable(path) == 1)
    session.presentation:close_cell(cell.id)
    assert(not vim.api.nvim_buf_is_valid(output) and vim.fn.filereadable(path) == 0)
    assert(session.controller.kernel_state == 'on')
    vim.api.nvim_buf_set_lines(buf, 1, -2, false, { '%%javascript', 'element.textContent = "JavaScript from Jusi";' })
    session.model:flush()
    url, done, response, failure = nil, nil, nil, nil
    jusi.execute(buf, 1, function(r, f) done, response, failure = true, r, f end)
    wait_for(function() return done and url ~= nil end, 'JavaScript MIME through HTTP/SSE')
    assert(not failure and response.execution.outcome == 'succeeded', vim.inspect(failure))
    local document = table.concat(vim.fn.readfile(vim.uri_to_fname(url)), '\n')
    local encoded = assert(document:match('src="data:text/javascript;base64,([A-Za-z0-9+/=]+)"'))
    assert(vim.base64.decode(encoded):find('element.textContent = "JavaScript from Jusi";', 1, true))
    assert(vim.api.nvim_get_current_win() == origin)

    vim.api.nvim_buf_set_lines(buf, 1, -2, false, {
      'from IPython.display import clear_output, display, update_display',
      "display({'text/plain': 'discard'}, raw=True)",
      "clear_output(wait=True)",
      "display({'text/plain': 'before'}, raw=True, display_id='live')",
      "display({'text/plain': ' fixed'}, raw=True)",
      "update_display({'text/plain': 'after'}, raw=True, display_id='live')",
    })
    session.model:flush()
    done, response, failure = nil, nil, nil
    jusi.execute(buf, 1, function(r, f) done, response, failure = true, r, f end)
    wait_for(function()
      local rendered = session.presentation:buffer_for_cell(cell.id)
      return done and rendered and vim.api.nvim_buf_get_lines(rendered, 0, 1, false)[1] == 'after fixed'
    end, 'IPython clear and targeted display update through HTTP/SSE')
    assert(not failure and response.execution.outcome == 'succeeded', vim.inspect(failure))
  end, debug.traceback)
  if buf and jusi._sessions[buf] then jusi._destroy_session(buf)
  elseif service then service:stop() end
  vim.api.nvim_del_user_command('JusiWebBuffer')
  assert(ok, err)
  print('Rich web presentation through service/kernel/SSE passed')
end
return M
