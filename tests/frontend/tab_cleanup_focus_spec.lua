local M = {}
function M.run()
  local jusi = require('jusi')
  jusi.setup()
  local initial = vim.api.nvim_get_current_tabpage()
  vim.cmd.tabnew()
  local source = vim.api.nvim_get_current_tabpage()
  local notebooks = {}
  for index = 1, 2 do
    local buf = vim.api.nvim_create_buf(false, true)
    vim.bo[buf].bufhidden = 'hide'
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { '╭──', tostring(index), '╰──' })
    local model = require('jusi.notebook').attach(buf)
    local mode = require('jusi.cellmode').new({ model = model })
    notebooks[index] = { buf = buf, model = model, mode = mode }
    if index == 1 then vim.api.nvim_set_current_buf(buf)
    else vim.api.nvim_open_win(buf, false, { split = 'below' }) end
  end
  vim.cmd.tabnew()
  local scratch = vim.api.nvim_get_current_buf()
  local focus = vim.api.nvim_get_current_win()
  local mirrors = {}
  for _, item in ipairs(notebooks) do
    mirrors[#mirrors+1] = vim.api.nvim_open_win(item.buf, false, { split = 'left' })
  end
  vim.api.nvim_feedkeys('\\Q', 'xt', false)
  assert(vim.api.nvim_get_current_win() == focus and vim.api.nvim_get_current_buf() == scratch)
  for _, win in ipairs(mirrors) do assert(not vim.api.nvim_win_is_valid(win)) end
  for _, item in ipairs(notebooks) do assert(#vim.fn.win_findbuf(item.buf) == 1) end
  -- No notebook in this tab: do not reach into the source tab.
  local notify = vim.notify
  local warning
  vim.notify = function(message) warning = message end
  jusi.close_tab()
  vim.notify = notify
  assert(warning == 'current tab has no Jusi notebook or output')
  -- Repeated setup respects a custom global binding, even from a notebook.
  local original = vim.fn.maparg('\\Q', 'n', false, true)
  local custom = function() end
  vim.keymap.set('n', '\\Q', custom)
  jusi.setup()
  assert(vim.fn.maparg('\\Q', 'n', false, true).callback == custom)
  vim.fn.mapset('n', false, original)
  vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(source)
  for _, item in ipairs(notebooks) do item.mode:close(); item.model:detach() end
  vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(initial)
  for _, item in ipairs(notebooks) do vim.api.nvim_buf_delete(item.buf, { force = true }) end
  vim.api.nvim_buf_delete(scratch, { force = true })
end
return M
