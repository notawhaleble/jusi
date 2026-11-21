local M = {}
local protocol = require('jusi.protocol')
local actions = require('jusi.editor_actions')
local function read(name)
  return vim.json.decode(table.concat(vim.fn.readfile('protocol/fixtures/v1/' .. name .. '.json'), '\n'))
end
function M.run()
  assert(protocol.validate_editor_action(read('valid/open-url-slot'), 'open_url'))
  assert(protocol.validate_editor_action(read('valid/close-url'), 'close_url'))
  assert(not protocol.validate_editor_action(read('invalid/open-url-slot'), 'open_url'))
  for _, name in ipairs({ 'missing-slot', 'text', 'url' }) do
    assert(not protocol.validate_editor_action(read('invalid/close-url-' .. name), 'close_url'))
  end
  local original = vim.api.nvim_get_current_win()
  local source_buf = vim.api.nvim_get_current_buf()
  local source_lines = vim.api.nvim_buf_get_lines(source_buf, 0, -1, false)
  local source = vim.api.nvim_open_win(source_buf, true, { split = 'below', win = original })
  local other_buf = vim.api.nvim_create_buf(false, true)
  local other_win = vim.api.nvim_open_win(other_buf, false, { split = 'right', win = original })
  local opened = {}
  vim.api.nvim_create_user_command('JusiWebBuffer', function(command)
    local buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_set_current_buf(buf)
    table.insert(opened, buf)
  end, { nargs = '?' })
  local first, second = {}, {}
  local function open(slots, url, win, source_buffer)
    return assert(actions.apply({ action = 'open_url', text = '', url = url, slot = 'page' },
      { win = win or source, source_buf = source_buffer or source_buf, url_slots = slots }))
  end
  local function close(slots)
    assert(actions.apply({ action = 'close_url', text = '', slot = 'page' }, { url_slots = slots }))
  end
  local ok, failure = xpcall(function()
    local borrowed = { buf = other_buf, lifetime = 'independent' }
    first.page = borrowed
    close(first)
    assert(first.page == borrowed and vim.api.nvim_buf_is_valid(other_buf))
    local count = #vim.api.nvim_list_wins()
    local a = open(first, 'https://one.test')
    assert(vim.api.nvim_win_get_buf(source) == a and #vim.api.nvim_list_wins() == count,
      'managed page did not reuse its source window')
    assert(first.page == borrowed, "URL replacement overwrote a document binding")
    local other = open(second, 'https://other.test', other_win, other_buf)
    -- Hidden projections still belong to their slot and must be deleted.
    for _, win in ipairs(vim.fn.win_findbuf(a)) do vim.api.nvim_win_close(win, true) end
    assert(vim.api.nvim_buf_is_valid(a))
    source = vim.api.nvim_open_win(source_buf, true, { split = 'below', win = original })
    local b = open(first, 'https://two.test')
    assert(not vim.api.nvim_buf_is_valid(a) and b ~= a)
    assert(vim.api.nvim_buf_is_valid(other), 'another client lost its page')
    -- Native close cannot end the source terminal/client; a repeated close is OK.
    vim.api.nvim_buf_delete(b, { force = true })
    close(first); close(first)
    assert(vim.api.nvim_buf_is_valid(source_buf))
    assert(vim.deep_equal(vim.api.nvim_buf_get_lines(source_buf, 0, -1, false), source_lines))
    assert(vim.api.nvim_buf_is_valid(other))
    source = vim.api.nvim_open_win(source_buf, true, { split = 'below', win = original })
    local c = open(first, 'https://three.test')
    -- Replace directly while the terminal is covered by its managed page.
    local d = open(first, 'https://four.test')
    assert(not vim.api.nvim_buf_is_valid(c) and vim.api.nvim_win_get_buf(source) == d)
    close(first)
    assert(not vim.api.nvim_buf_is_valid(d) and vim.api.nvim_win_get_buf(source) == source_buf)
    -- Failed deletion must keep replacement authority so a later open retries.
    local retry = open(first, 'https://retry.test')
    local delete = vim.api.nvim_buf_delete
    vim.api.nvim_buf_delete = function(buf, opts)
      if buf == retry then error('temporary deletion failure') end
      return delete(buf, opts)
    end
    local delivered = actions.apply({ action = 'close_url', text = '', slot = 'page' }, { url_slots = first })
    vim.api.nvim_buf_delete = delete
    assert(not delivered and first['url:page'] and not first['url:page'].closed, 'failed close forgot a live page')
    local retried = open(first, 'https://retried.test')
    assert(not vim.api.nvim_buf_is_valid(retry), 'replacement did not retry page deletion')
    close(first)
    assert(not vim.api.nvim_buf_is_valid(retried))
    -- A buffer can be displayed in multiple tabs. Retire it everywhere.
    local mirrored = open(first, 'https://mirrored.test')
    vim.cmd('tabnew')
    local mirror = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(mirror, mirrored)
    vim.api.nvim_set_current_win(source)
    local replacement = open(first, 'https://replacement.test')
    assert(not vim.api.nvim_buf_is_valid(mirrored), 'another tab retained the old page')
    assert(vim.api.nvim_win_get_buf(mirror) == source_buf, 'another tab did not restore its source')
    assert(vim.api.nvim_win_get_buf(source) == replacement)
    vim.api.nvim_win_close(mirror, true)
    close(first)
    -- Closing a hidden page must leave a repurposed window alone.
    local hidden = open(first, 'https://hidden.test')
    local unrelated = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_win_set_buf(source, unrelated)
    close(first)
    assert(not vim.api.nvim_buf_is_valid(hidden) and vim.api.nvim_win_get_buf(source) == unrelated)
    vim.api.nvim_win_set_buf(source, source_buf)
    vim.api.nvim_buf_delete(unrelated, { force = true })
    assert(not actions.apply({ action = 'close_url', text = '', slot = 'page' }))
    local before = #opened
    assert(not actions.apply({ action = 'open_url', text = '', url = 'https://test', slot = '../bad' },
      { win = source, url_slots = first }))
    assert(#opened == before)
    vim.api.nvim_create_user_command('JusiWebBuffer', function()
      vim.api.nvim_set_current_buf(vim.api.nvim_create_buf(false, true))
      error('host rejected managed URL')
    end, { nargs = '?', force = true })
    local buffers, count = #vim.api.nvim_list_bufs(), #vim.api.nvim_list_wins()
    assert(not actions.apply({ action = 'open_url', text = '', url = 'https://failure.test', slot = 'page' },
      { win = source, source_buf = source_buf, url_slots = first }))
    assert(vim.api.nvim_win_get_buf(source) == source_buf and #vim.api.nvim_list_wins() == count,
      'host failure did not restore the source window')
    assert(#vim.api.nvim_list_bufs() == buffers and first['url:page'] == nil, 'host failure leaked a page')
  end, debug.traceback)
  close(first); close(second)
  if vim.api.nvim_win_is_valid(source) then vim.api.nvim_win_close(source, true) end
  if vim.api.nvim_win_is_valid(other_win) then vim.api.nvim_win_close(other_win, true) end
  vim.api.nvim_buf_delete(other_buf, { force = true })
  vim.api.nvim_del_user_command('JusiWebBuffer')
  assert(ok, failure)
end
return M
