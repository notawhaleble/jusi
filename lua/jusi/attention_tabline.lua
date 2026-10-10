-- Temporarily enhance the native tabline; custom tablines retain ownership.
local M = {}
local expression = "%!v:lua.require'jusi.attention_tabline'.render()"
local summaries, enabled, started, saved_showtabline = {}, true, false, nil
local scheduled = false
local function highlights()
  vim.api.nvim_set_hl(0, 'JusiTabAttention', { fg = '#201800', bg = '#e5b454', bold = true,
    ctermfg = 0, ctermbg = 179, default = true })
  vim.api.nvim_set_hl(0, 'JusiTabNotice', { fg = '#102030', bg = '#80b8e0', bold = true,
    ctermfg = 0, ctermbg = 110, default = true })
end
function M.summary(tab)
  return vim.deepcopy(summaries[tab] or { count = 0, action_required = 0, notice = 0 })
end
function M.refresh()
  summaries = require('jusi.attention').tab_summaries()
  if enabled and next(summaries) and vim.o.tabline == '' then
    saved_showtabline = vim.o.showtabline
    vim.o.tabline, vim.o.showtabline = expression, 2
  elseif (not enabled or not next(summaries)) and vim.o.tabline == expression then
    vim.o.tabline = ''
    if vim.o.showtabline == 2 then vim.o.showtabline = saved_showtabline or 1 end
    saved_showtabline = nil
  end
  vim.cmd.redrawtabline()
  vim.api.nvim_exec_autocmds('User', { pattern = 'JusiAttentionChanged', modeline = false })
end
local function escape(value) return value:gsub('[%c]', ' '):gsub('%%', '%%%%') end
function M.render()
  local parts, current = {}, vim.api.nvim_get_current_tabpage()
  local tabs = vim.api.nvim_list_tabpages()
  for index, tab in ipairs(tabs) do
    local summary = M.summary(tab)
    local hl = summary.action_required > 0 and 'JusiTabAttention'
      or summary.notice > 0 and 'JusiTabNotice' or tab == current and 'TabLineSel' or 'TabLine'
    local wins, modified = vim.api.nvim_tabpage_list_wins(tab), false
    local count = 0
    for _, win in ipairs(wins) do
      if vim.api.nvim_win_get_config(win).relative == '' then
        count = count + 1
        modified = modified or vim.bo[vim.api.nvim_win_get_buf(win)].modified
      end
    end
    local buf = vim.api.nvim_win_get_buf(vim.api.nvim_tabpage_get_win(tab))
    local name = vim.fn.fnamemodify(vim.api.nvim_buf_get_name(buf), ':t')
    if name == '' then name = '[No Name]' end
    local badge = summary.count > 0 and (' !%d'):format(summary.count) or ''
    parts[#parts+1] = ('%%#%s#%%%dT %s%s%s%s '):format(hl, index,
      count > 1 and tostring(count) .. ' ' or '', modified and '+' or '', escape(name), badge)
  end
  parts[#parts+1] = '%T%#TabLineFill#%='
  if #tabs > 1 then parts[#parts+1] = '%999X X ' end
  return table.concat(parts)
end
function M.setup(value)
  if value ~= nil then
    assert(type(value) == 'boolean', 'attention.tabline must be boolean')
    enabled = value
  end
  if started then M.refresh(); return end
  started = true
  highlights()
  local group = vim.api.nvim_create_augroup('jusi_attention_tabline', { clear = true })
  vim.api.nvim_create_autocmd('ColorScheme', { group = group, callback = highlights })
  vim.api.nvim_create_autocmd({ 'TabEnter', 'TabNew', 'TabClosed', 'BufWinEnter', 'BufWinLeave', 'WinClosed' }, {
    group = group, callback = function()
      if scheduled then return end
      scheduled = true
      vim.schedule(function() scheduled = false; M.refresh() end)
    end,
  })
  M.refresh()
end
return M
