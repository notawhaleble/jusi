local M = {}
local Mode = {}
Mode.__index = Mode
local instances = {}
local clipboard
local function current_map(buf, key)
  return vim.api.nvim_buf_call(buf, function() return vim.fn.maparg(key, 'n', false, true) end)
end
function Mode:map(key, callback)
  local old = current_map(self.editor.model.buf, key)
  self.maps[key] = { old = old.buffer == 1 and old or nil, callback = callback }
  vim.keymap.set('n', key, callback, { buffer = self.editor.model.buf, silent = true, desc = 'Jusi cell mode' })
end
function Mode:unmap(key)
  local saved = self.maps[key]
  if not saved then return end
  local buf = self.editor.model.buf
  local now = current_map(buf, key)
  if now.callback == saved.callback then
    vim.keymap.del('n', key, { buffer = buf })
    if saved.old then vim.api.nvim_buf_call(buf, function() vim.fn.mapset('n', false, saved.old) end) end
  end
  self.maps[key] = nil
end
function Mode:refresh_marks()
  vim.api.nvim_exec_autocmds('User', { pattern = 'JusiCellModeChanged', data = { buf = self.editor.model.buf } })
end
function Mode:set(enabled)
  local buf = self.editor.model.buf
  self.enabled = enabled
  vim.b[buf].jusi_cell_mode = enabled
  vim.b[buf].jusi_cell_mode_active = enabled and not self.inserting
  for key in pairs(vim.deepcopy(self.maps)) do if key ~= '<Space>' then self:unmap(key) end end
  if enabled then
    local actions = {
      j = function() self:move(1, true) end,
      k = function() self:move(-1, true) end,
      ['<CR>'] = function() require('jusi').submit() end,
      H = function() self.editor.history:toggle() end,
      C = function() self:edit() end, X = function() self:delete() end,
      Y = function() self:copy() end, P = function() self:paste() end,
      B = function() self:insert(false) end,
      Q = function() require('jusi').close() end,
      S = function() require('jusi').park() end,
      ['<C-P>'] = function() self:history_relative(-1) end,
      ['<C-N>'] = function() self:history_relative(1) end,
    }
    for key, callback in pairs(actions) do self:map(key, callback) end
  end
  self:refresh_marks()
end
function Mode:cell()
  local model = self.editor.model
  local cell = model:cell_at_row(vim.api.nvim_win_get_cursor(0)[1] - 1)
  return cell, cell and model:cell_snapshot(cell)
end
function Mode:move(direction, history)
  self.editor.history:refresh()
  return require('jusi.navigation').move(self.editor.model, direction, vim.v.count1, history)
end
function Mode:edit()
  local _, s = self:cell()
  if not s or not s.valid then return false end
  local buf, first = self.editor.model.buf, s.body_start_row
  local header = vim.api.nvim_buf_get_lines(buf, first, first + 1, false)[1] or ''
  if header:match('^%s*%%%%[%a][%w_-]*') then first = first + 1 end
  vim.api.nvim_buf_set_lines(buf, first, s.body_end_row, false, { '' })
  self.editor.model:flush()
  vim.api.nvim_win_set_cursor(0, { first + 1, 0 })
  vim.cmd('startinsert')
  return true
end
function Mode:copy()
  local _, s = self:cell()
  if not s or not s.valid then return false end
  clipboard = vim.api.nvim_buf_get_lines(self.editor.model.buf, s.open_row, s.end_row, false)
  return true
end
function Mode:delete()
  local cell, s = self:cell()
  if not s then return false end
  local next_id = cell.next and cell.next.id or cell.prev and cell.prev.id
  local model = self.editor.model
  vim.api.nvim_buf_set_lines(model.buf, s.open_row, s.end_row, false, {})
  model:flush()
  local target = next_id and model:cell_snapshot(next_id)
  if target then vim.api.nvim_win_set_cursor(0, { require('jusi.navigation').body_row(target) + 1, 0 }) end
  return true
end
function Mode:insert(above, lines)
  local _, s = self:cell()
  if s and not s.valid then return false end
  local model = self.editor.model
  local row = s and (above and s.open_row or s.end_row) or vim.api.nvim_win_get_cursor(0)[1] - 1
  vim.api.nvim_buf_set_lines(model.buf, row, row, false, lines or { '╭──', '', '╰──' })
  model:flush()
  local inserted = model:cell_at_row(row)
  local snapshot = inserted and model:cell_snapshot(inserted)
  vim.api.nvim_win_set_cursor(0, { snapshot and require("jusi.navigation").body_row(snapshot) + 1 or row + 1, 0 })
  if not lines then vim.cmd('startinsert') end
  return true
end
function Mode:paste()
  if not clipboard then return false end
  return self:insert(false, clipboard)
end
function Mode:history_relative(direction)
  local cell, s = self:cell()
  if not s or not s.valid or #s.history_entries == 0 then return false end
  local body = self.editor.model:body(cell)
  if (body[1] or ''):match('^%s*%%%%[%a][%w_-]*') then table.remove(body, 1) end
  local entries = self.editor.model:history(cell)
  local index = 1
  for i, entry in ipairs(entries) do
    if vim.deep_equal(body, entry) then index = math.max(1, math.min(#entries, i - direction)); break end
  end
  vim.api.nvim_win_set_cursor(0, { s.history_entries[index].start_row, 0 })
  return self.editor.history:apply()
end
function Mode:close()
  if self.closed then return end
  self.closed = true
  instances[self.editor.model.buf] = nil
  vim.api.nvim_del_augroup_by_id(self.group)
  if vim.api.nvim_buf_is_valid(self.editor.model.buf) then
    for key in pairs(vim.deepcopy(self.maps)) do self:unmap(key) end
    vim.b[self.editor.model.buf].jusi_cell_mode_active = false
  end
end
function M.new(editor)
  local buf = editor.model.buf
  local self = setmetatable({ editor = editor, maps = {} }, Mode)
  instances[buf] = self
  self.group = vim.api.nvim_create_augroup('jusi_cellmode_' .. editor.model.notebook_id, { clear = true })
  self:map('<Space>', function() self:set(not self.enabled) end)
  for _, event in ipairs({ 'InsertEnter', 'InsertLeave' }) do
    vim.api.nvim_create_autocmd(event, { group = self.group, buffer = buf, callback = function()
      self.inserting = event == 'InsertEnter'
      vim.b[buf].jusi_cell_mode_active = self.enabled and not self.inserting
      if self.enabled then self:refresh_marks() end
    end })
  end
  self:set(vim.b[buf].jusi_cell_mode == true)
  return self
end
function M.get(buf) return instances[buf or vim.api.nvim_get_current_buf()] end
function M.command(action, argument)
  local self = M.get()
  if not self then vim.notify('Jusi: current buffer is not a notebook', vim.log.levels.WARN); return end
  if action == 'toggle' then self:set(not self.enabled)
  else return self[action](self, argument) end
end
return M
