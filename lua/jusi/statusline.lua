local M = {}
local expression = '%!v:lua.require("jusi.statusline").render()'
local saved, started, queued = {}, false, false
local retained = {}
local function highlights()
  local light = vim.o.background == 'light'
  local palettes = {
    JusiStatusKernelNeutral = light and { '#50575e', '#e1e4e8', 240, 254 } or { '#c0c6cd', '#353b43', 250, 237 },
    JusiStatusKernelOn = light and { '#294b39', '#d9e8de', 22, 194 } or { '#bad7c4', '#30463a', 151, 236 },
    JusiStatusKernelStale = light and { '#66532d', '#eee4ce', 94, 230 } or { '#d8c397', '#4b4231', 180, 238 },
  }
  for name, palette in pairs(palettes) do
    vim.api.nvim_set_hl(0, name, { fg = palette[1], bg = palette[2], ctermfg = palette[3], ctermbg = palette[4] })
  end
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
-- Preserve only an observed display snapshot, never a live controller/resource.
function M.retain(session)
  local ctl = session.controller
  if ctl.supervisor_id then retained[session.buf] = { state = ctl.kernel_state, target = session.target_alias } end
  M.redraw()
end
function M.kernel_view(buf)
  local jusi = require('jusi')
  local session, previous = jusi._sessions[buf], retained[buf]
  local ctl = session and session.controller
  local ticket = jusi._starts[buf]
  local state = ctl and ctl.supervisor_id and ctl.kernel_state or (previous and previous.state or '—')
  local operation = ticket and (ticket.cancelled and 'stopping' or 'starting') or (session and session.stopping and 'stopping' or nil)
  -- Do not expose the service's pre-start inspection as a completed start.
  -- Keep the pre-operation display until the kernel publishes on or start ends.
  local confirmed_on = ctl and ctl.supervisor_id and ctl.kernel_state == 'on' and ctl.transport_state == 'connected'
  if ticket and not confirmed_on then state = ticket.status_initial or '—' end
  local transport = ctl and ctl.transport_state or nil
  local stale = state ~= '—' and ((ctl and transport ~= 'connected') or (not ctl and state == 'on'))
  local color = stale and 'JusiStatusKernelStale' or (state == 'on' and 'JusiStatusKernelOn' or 'JusiStatusKernelNeutral')
  -- An observed off stays neutral during the final transport teardown.
  if operation and state ~= 'on' then color = 'JusiStatusKernelNeutral'; stale = false end
  return { state = state, color = color, stale = stale, operation = operation,
    transport = transport, target = (session and session.target_alias) or (ticket and ticket.alias) or (previous and previous.target) }
end
function M.render(win)
  win = win or tonumber(vim.g.statusline_winid) or vim.api.nvim_get_current_win()
  if not vim.api.nvim_win_is_valid(win) then return '' end
  local buf = vim.api.nvim_win_get_buf(win)
  local sessions = require('jusi')._sessions
  local session = sessions[buf]
  local role = vim.b[buf].jusi_role
  local base = win == vim.api.nvim_get_current_win() and 'StatusLine' or 'StatusLineNC'
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
    local view = M.kernel_view(buf)
    parts[#parts + 1] = ' | %#' .. view.color .. '# kernel: ' .. view.state .. ' ' .. reset
    if view.stale then parts[#parts + 1] = '(last known)' end
    if view.operation then parts[#parts + 1] = ' | ' .. view.operation .. '…'
    elseif view.transport and view.transport ~= 'connected' then parts[#parts + 1] = ' | transport: ' .. escape(view.transport)
    elseif view.stale then parts[#parts + 1] = ' | transport: disconnected' end
    if view.target and view.state ~= 'off' then parts[#parts + 1] = ' | ' .. escape(view.target) end
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
  vim.api.nvim_create_autocmd('BufWipeout', { group = group, callback = function(args) retained[args.buf] = nil end })
  vim.api.nvim_create_autocmd('WinClosed', { group = group, callback = function(args) saved[tonumber(args.match)] = nil end })
  vim.api.nvim_create_autocmd('ModeChanged', { group = group, callback = M.redraw })
  vim.api.nvim_create_autocmd('ColorScheme', { group = group, callback = function() highlights(); M.redraw() end })
  vim.api.nvim_create_autocmd('OptionSet', { group = group, pattern = 'background', callback = function() highlights(); M.redraw() end })
  vim.api.nvim_create_autocmd('User', { group = group, pattern = 'JusiCellModeChanged', callback = M.redraw })
  for _, win in ipairs(vim.api.nvim_list_wins()) do M.refresh(win) end
end
return M
