local M = {}
function M.run()
  require('jusi').setup()
  local fixture = vim.json.decode(table.concat(vim.fn.readfile('protocol/fixtures/v1/valid/javascript-bundle.json'), '\n'))
  assert(require('jusi.protocol').validate_event(fixture))
  local original = vim.api.nvim_get_current_win()
  local notebook = vim.api.nvim_get_current_buf()
  local p = require('jusi.presentation').new({ notebook_id = 'nb_web_regression', notebook_buf = notebook })
  local urls = {}
  vim.api.nvim_create_user_command('JusiWebBuffer', function(command)
    urls[#urls + 1] = command.args
    vim.api.nvim_set_current_buf(vim.api.nvim_create_buf(true, true))
  end, { nargs = '?' })
  local function rich(id, mime, data, display_id, kind)
    return p:write('cell', { execution_id = 'exe_web', bundle_id = id, bundle_end = true,
      display_id = display_id, output_kind = kind or 'display',
      media_type = 'application/vnd.jusi.mime-bundle.v1+json', data = vim.json.encode({ [mime] = data }) })
  end
  local ok, err = xpcall(function()
    p:start_execution('cell', { execution_id = 'exe_web' })
    local text = assert(p:write('cell', { execution_id = 'exe_web', media_type = 'text/plain', data = 'Building the font cache\r\n' }))
    local win = assert(require('jusi.presentation.window').find(text.buf))
    local count = #vim.api.nvim_list_wins()
    local plot = assert(rich('plot', 'image/svg+xml', '<svg xmlns="http://www.w3.org/2000/svg"/>', 'figure'))
    assert(#vim.api.nvim_list_wins() == count, 'text followed by rich output created a second split')
    assert(vim.api.nvim_win_get_buf(win) == plot.buf, 'plot did not reuse the text output window')
    assert(vim.api.nvim_get_current_win() == original)
    assert(vim.api.nvim_buf_is_valid(text.buf), 'switching presentation destroyed earlier text')
    p:write('cell', { execution_id = 'exe_web', media_type = 'text/plain', data = 'finished\r\n' })
    assert(#vim.api.nvim_list_wins() == count and vim.api.nvim_win_get_buf(win) == plot.buf)
    -- The host appends a normal statusline item; evaluate the composed option,
    -- including inactive-window context, instead of calling our renderer alone.
    _G.jusi_test_browser_status = function() return '[Profile: default | Text ready]' end
    local suffix = ' %{v:lua.jusi_test_browser_status()}'
    vim.api.nvim_set_current_win(win)
    require('jusi.statusline').refresh(win)
    vim.wo[win].statusline = vim.wo[win].statusline .. suffix
    local function check_status()
      local value = vim.api.nvim_eval_statusline(vim.wo[win].statusline, { winid = win, maxwidth = 200 }).str
      assert(value:find('[Profile: default | Text ready]', 1, true), value)
    end
    check_status()
    require('jusi.statusline').refresh(win)
    check_status()
    vim.api.nvim_set_current_win(original)
    check_status()
    vim.api.nvim_set_current_win(win)
    check_status()
    vim.api.nvim_set_current_win(original)
    local js = 'element.textContent = "Hello </script> α";'
    local script = assert(rich('javascript', 'application/javascript', js))
    assert(vim.api.nvim_win_get_buf(win) == script.buf and #vim.api.nvim_list_wins() == count)
    local html = table.concat(vim.fn.readfile(vim.uri_to_fname(urls[#urls])), '\n')
    local encoded = assert(html:match('src="data:text/javascript;base64,([A-Za-z0-9+/=]+)"'))
    assert(vim.base64.decode(encoded) == js, 'JavaScript source was changed or parsed as HTML')
    assert(html:find('id="jusi-output"', 1, true))
    local old_path = vim.uri_to_fname(urls[1])
    rich('plot-update', 'text/html', '<b>updated</b>', 'figure', 'update')
    assert(vim.fn.filereadable(old_path) == 0)
    assert(vim.api.nvim_win_get_buf(win) == script.buf and #vim.api.nvim_list_wins() == count)
    assert(#urls == 3, 'updating a hidden figure reran another rich display')
    -- A failed host invocation rolls back the reused window, preserving output.
    vim.api.nvim_create_user_command('JusiWebBuffer', function()
      vim.api.nvim_set_current_buf(vim.api.nvim_create_buf(true, true))
      error('host failed')
    end, { nargs = '?', force = true })
    local buffers = #vim.api.nvim_list_bufs()
    rich('failed', 'text/html', '<b>no</b>')
    assert(#vim.api.nvim_list_wins() == count and vim.api.nvim_win_get_buf(win) == script.buf)
    assert(#vim.api.nvim_list_bufs() == buffers, 'host failure leaked a buffer')
  end, debug.traceback)
  p:close()
  vim.api.nvim_del_user_command('JusiWebBuffer')
  _G.jusi_test_browser_status = nil
  assert(ok, err)
end
return M
