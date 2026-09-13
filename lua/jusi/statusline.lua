local M = {}
local expression = '%!v:lua.require("jusi.statusline").render()'
local saved, started, queued = {}, false, false
local function highlights()
  local light = vim.o.background == 'light'
  vim.api.nvim_set_hl(0, 'JusiStatusUnconnected', {
    fg = light and '#505050' or '#c6c6c6', bg = light and '#d0d0d0' or '#3a3a3a',
    ctermfg = light and 239 or 251, ctermbg = light and 252 or 237,
  })
  vim.api.nvim_set_hl(0, 'JusiStatusCellMode', {
    fg = light and '#302040' or '#f0e6ff', bg = light and '#cbb9e8' or '#5b457a',
    ctermfg = light and 236 or 255, ctermbg = light and 182 or 60, bold = true,
  })
end
local function notebook(buf)
  return vim.bo[buf].filetype == 'jusi' or require('jusi.cellmode').get(buf) ~= nil
end
local function relevant(buf)
  return notebook(buf) or vim.b[buf].jusi_role == 'output' or vim.b[buf].jusi_role == 'interactive_terminal'
end
local function escape(value) return tostring(value):gsub('%%', '%%%%'):gsub('[\r\n]', ' ') end
function M.redraw()
  if queued then return end
  queued = true
  vim.schedule(function() queued = false; vim.cmd.redrawstatus() end)
end
function M.refresh(win)
  win = win or vim.api.nvim_get_current_win()
  if not vim.api.nvim_win_is_valid(win) then return end
  local current = vim.api.nvim_get_option_value('statusline', { win = win, scope = 'local' })
  if relevant(vim.api.nvim_win_get_buf(win)) then
    if saved[win] == nil then
      saved[win] = current == expression and '' or current
      vim.api.nvim_set_option_value('statusline', expression, { win = win, scope = 'local' })
    end
  elseif saved[win] ~= nil then
    if current == expression then vim.api.nvim_set_option_value('statusline', saved[win], { win = win, scope = 'local' }) end
    saved[win] = nil
  end
end
function M.render(win)
  win = win or tonumber(vim.g.statusline_winid) or vim.api.nvim_get_current_win()
  if not vim.api.nvim_win_is_valid(win) then return '' end
  local buf = vim.api.nvim_win_get_buf(win)
  local sessions = require('jusi')._sessions
  local session = sessions[buf]
  local role = vim.b[buf].jusi_role
  local unknown = not session or not session.controller.supervisor_id
  local base = (role ~= 'output' and role ~= 'interactive_terminal' and unknown) and 'JusiStatusUnconnected' or 'StatusLine'
  local reset = '%#' .. base .. '#'
  local parts = { reset .. ' ' }
  if role == 'output' or role == 'interactive_terminal' then
    local number = vim.b[buf].jusi_output_number
    parts[#parts + 1] = (role == 'output' and 'output ' or 'client ') .. (number or '?')
    for _, candidate in pairs(sessions) do
      if candidate.model.notebook_id == vim.b[buf].jusi_notebook_id then session = candidate; break end
    end
    if session then parts[#parts + 1] = ' | ' .. escape(vim.fn.fnamemodify(vim.api.nvim_buf_get_name(session.buf), ':t'):gsub('%.vipynb$', '')) end
    if role == 'interactive_terminal' then
      parts[#parts + 1] = win == vim.api.nvim_get_current_win() and vim.fn.mode():sub(1,1) == 't' and ' | input' or ' | normal'
    end
  else
    local name = vim.fn.fnamemodify(vim.api.nvim_buf_get_name(buf), ':t')
    parts[#parts + 1] = escape(name ~= '' and name or '[No Name]') .. (vim.bo[buf].modified and ' [+]' or '')
    local ctl = session and session.controller
    local transport = ctl and ctl.transport_state or 'disconnected'
    local state = ctl and ctl.supervisor_id and ctl.kernel_state or '—'
    local color = unknown and 'JusiStatusUnconnected' or (transport ~= 'connected' and 'DiagnosticWarn' or (state == 'on' and 'DiagnosticOk' or 'DiagnosticError'))
    parts[#parts + 1] = ' | %#' .. color .. '#kernel: ' .. state .. reset
    if transport ~= 'connected' then
      if state ~= '—' then parts[#parts + 1] = ' (last known)' end
      parts[#parts + 1] = ' | transport: ' .. escape(transport)
    end
    if session and session.target_alias then parts[#parts + 1] = ' | ' .. escape(session.target_alias) end
    if vim.b[buf].jusi_cell_mode_active then parts[#parts + 1] = ' | %#JusiStatusCellMode# mode:cell ' .. reset end
  end
  parts[#parts + 1] = '%=%l:%c '
  return table.concat(parts)
end
function M.setup()
  if started then return end
  started = true
  highlights()
  local group = vim.api.nvim_create_augroup('jusi_statusline', { clear = true })
  vim.api.nvim_create_autocmd({ 'BufWinEnter', 'WinEnter', 'FileType' }, { group = group, callback = function() M.refresh() end })
  vim.api.nvim_create_autocmd('WinClosed', { group = group, callback = function(args) saved[tonumber(args.match)] = nil end })
  vim.api.nvim_create_autocmd('ModeChanged', { group = group, callback = M.redraw })
  vim.api.nvim_create_autocmd('ColorScheme', { group = group, callback = function() highlights(); M.redraw() end })
  vim.api.nvim_create_autocmd('OptionSet', { group = group, pattern = 'background', callback = function() highlights(); M.redraw() end })
  vim.api.nvim_create_autocmd('User', { group = group, pattern = 'JusiCellModeChanged', callback = M.redraw })
  for _, win in ipairs(vim.api.nvim_list_wins()) do M.refresh(win) end
end
return M
