local M = {}
local protocol = require('jusi.protocol')
local function read(name)
  return vim.json.decode(table.concat(vim.fn.readfile('protocol/fixtures/v1/' .. name .. '.json'), '\n'))
end
function M.run()
  assert(protocol.validate_editor_action(read('valid/open-url'), 'open_url'))
  for _, name in ipairs({ 'scheme', 'newline', 'empty-host' }) do
    assert(not protocol.validate_editor_action(read('invalid/open-url-' .. name), 'open_url'))
  end
  assert(protocol.validate_event(read('valid/mime-bundle')))
  for _, name in ipairs({ 'boundary', 'media' }) do assert(not protocol.validate_event(read('invalid/mime-bundle-' .. name))) end
  local presentation = require('jusi.presentation').new({ notebook_id = 'nb_web' })
  local event = read('scenarios/web-presentation').events[1].payload
  presentation:start_execution('cell', { execution_id = event.execution_id })
  local fallback = assert(presentation:write('cell', event))
  assert(vim.wait(1000, function() return vim.api.nvim_buf_get_lines(fallback.buf, 0, 1, false)[1] == 'Figure' end))
  presentation:close()
  local urls = {}
  vim.api.nvim_create_user_command('JusiWebBuffer', function(command)
    urls[#urls + 1] = command.args
    vim.api.nvim_set_current_buf(vim.api.nvim_create_buf(true, true))
  end, { nargs = '?' })
  local ok, err = xpcall(function()
    local origin = vim.api.nvim_get_current_win()
    presentation:start_execution('cell', { execution_id = event.execution_id })
    local first = vim.tbl_extend('force', event, { data = event.data:sub(1, 15), bundle_end = false })
    assert(presentation:write('cell', first) == nil and #urls == 0)
    local surface = assert(presentation:write('cell', vim.tbl_extend('force', event, { data = event.data:sub(16) })))
    assert(vim.api.nvim_get_current_win() == origin, 'automatic output stole focus')
    assert(vim.b[surface.buf].jusi_cell_id == 'cell')
    assert(presentation:buffer_for_cell('cell') == surface.buf)
    local path = vim.uri_to_fname(urls[1])
    assert(vim.fn.filereadable(path) == 1)
    assert(table.concat(vim.fn.readfile(path), '\n'):find('data:image/svg+xml;base64,', 1, true))
    -- Native hide preserves content; focus/close use the same cell identity.
    for _, win in ipairs(vim.fn.win_findbuf(surface.buf)) do vim.api.nvim_win_close(win, true) end
    assert(vim.api.nvim_buf_is_valid(surface.buf) and vim.fn.filereadable(path) == 1)
    presentation:retire_execution('cell', event.execution_id)
    assert(not vim.api.nvim_buf_is_valid(surface.buf) and vim.fn.filereadable(path) == 0)
    assert(presentation:write('cell', event) == nil)
    -- Tab cleanup must see rich-only cells, including without a terminal.
    local next_event = vim.tbl_extend('force', event, { execution_id = 'exe_tab' })
    presentation:start_execution('cell', { execution_id = next_event.execution_id })
    local tab_surface = assert(presentation:write('cell', next_event))
    local notebook = vim.api.nvim_win_get_buf(origin)
    require('jusi.tab_cleanup').close({ buf = notebook, presentation = presentation,
      lifecycle = { close_cell = function(_, id, callback) presentation:close_cell(id); callback(true) end } })
    assert(not vim.api.nvim_buf_is_valid(tab_surface.buf))
    local url = 'https://example.com/a?x=1|let%20g:injected=1'
    local buf = assert(require('jusi.editor_actions').apply({ action = 'open_url', text = '', url = url }))
    assert(urls[3] == url and vim.g.injected == nil)
    require('jusi.presentation.window').close_for_buffer(buf)
    vim.api.nvim_buf_delete(buf, { force = true })
    -- Matplotlib's PNG representation and HTML use the same browser path.
    local png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII='
    for _, bundle in ipairs({ { ['image/png'] = png }, { ['text/html'] = '<h1>rich α</h1>' } }) do
      local rendered = assert(require('jusi.presentation.web').render(bundle, {}))
      local file = vim.uri_to_fname(urls[#urls])
      local document = table.concat(vim.fn.readfile(file), '\n')
      assert(document:find(bundle['text/html'] or ('data:image/png;base64,' .. png), 1, true))
      rendered:close()
      assert(vim.fn.filereadable(file) == 0)
    end
    local before = #vim.api.nvim_list_bufs()
    vim.api.nvim_create_user_command('JusiWebBuffer', function()
      vim.api.nvim_set_current_buf(vim.api.nvim_create_buf(true, true))
      error('host rejected URL')
    end, { nargs = '?', force = true })
    local failed = require('jusi.editor_actions').apply({ action = 'open_url', text = '', url = url })
    assert(failed == nil and #vim.api.nvim_list_bufs() == before, 'renderer failure leaked a buffer')
  end, debug.traceback)
  presentation:close()
  vim.api.nvim_del_user_command('JusiWebBuffer')
  assert(ok, err)
  local count = #vim.api.nvim_list_bufs()
  local buf, failure = require('jusi.editor_actions').apply(read('valid/open-url'))
  assert(buf == nil and failure:find('unavailable', 1, true))
  assert(#vim.api.nvim_list_bufs() == count, 'missing renderer leaked buffers')
end
return M
