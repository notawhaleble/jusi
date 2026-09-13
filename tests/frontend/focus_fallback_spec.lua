local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local original_tab = vim.api.nvim_get_current_tabpage()
  vim.cmd.tabnew()
  local first_tab = vim.api.nvim_get_current_tabpage()
  local first_buf = vim.api.nvim_get_current_buf()
  vim.bo[first_buf].filetype = 'jusi'
  local first_win = vim.api.nvim_get_current_win()
  vim.cmd.tabnew()
  local second_tab = vim.api.nvim_get_current_tabpage()
  local other_buf = vim.api.nvim_get_current_buf()
  local other_win = vim.api.nvim_get_current_win()
  local second_buf = vim.api.nvim_create_buf(true, false)
  vim.bo[second_buf].filetype = 'jusi'
  local second_win = vim.api.nvim_open_win(second_buf, false, { split = 'left', win = other_win })
  assert(jusi.toggle_focus() == second_buf)
  assert(vim.api.nvim_get_current_win() == second_win, 'fallback ignored notebook in current tab')
  vim.api.nvim_set_current_win(other_win)
  vim.api.nvim_win_close(second_win, true)
  assert(jusi.toggle_focus() == first_buf)
  assert(vim.api.nvim_get_current_win() == first_win, 'fallback did not cross tabs')
  vim.api.nvim_set_current_tabpage(second_tab)
  -- Default global mapping works outside Jusi, and setup preserves user maps.
  vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes('<C-\\><C-\\>', true, false, true), 'xt', false)
  assert(vim.api.nvim_get_current_win() == first_win)
  local key = '<C-\\><C-\\>'
  local original = vim.fn.maparg(key, 'n', false, true)
  vim.keymap.set('n', key, ':echo "custom"<CR>')
  jusi.setup()
  assert(vim.fn.maparg(key, 'n'):find('custom', 1, true))
  vim.fn.mapset('n', false, original)
  vim.api.nvim_set_current_tabpage(second_tab); vim.cmd.tabclose()
  vim.api.nvim_set_current_tabpage(first_tab); vim.cmd.tabclose()
  for _, buf in ipairs({first_buf, other_buf, second_buf}) do vim.api.nvim_buf_delete(buf, {force=true}) end
  vim.api.nvim_set_current_tabpage(original_tab)
end
return M
