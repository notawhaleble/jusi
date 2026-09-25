local M = {}
local History = {}
History.__index = History
local instances = {}
local function highlights()
  vim.api.nvim_set_hl(0, 'JusiHistoryFold', { fg = '#7f848e', ctermfg = 102, default = true })
end
local function position(self, mark)
  local p = vim.api.nvim_buf_get_extmark_by_id(self.model.buf, self.ns, mark, {})
  return p[1] and p[1] + 1
end
function M.foldtext()
  local self = instances[vim.api.nvim_get_current_buf()]
  local cell = self and self.model:cell_at_row(vim.v.foldstart - 1)
  local s = cell and self.model:cell_snapshot(cell)
  return { { 'history: ' .. (s and #s.history_entries or 0) .. ' entries', 'JusiHistoryFold' } }
end
function History:update(win, id)
  local state = self.windows[win]
  local old = state.cells[id]
  local cell = self.model:cell_by_id(id)
  local s = cell and self.model:cell_snapshot(cell)
  local valid = s and s.valid and s.history_row and s.close_row
  local restored = self.restored[tostring(win)]
  if not old and s and restored and restored[tostring(s.open_row)] ~= nil then
    old = { closed = restored[tostring(s.open_row)] }
    restored[tostring(s.open_row)] = nil
  end
  vim.api.nvim_win_call(win, function()
    local view = vim.fn.winsaveview()
    local closed = old == nil or old.closed
    if not old and valid and vim.fn.foldlevel(s.history_row + 1) > 0 then
      vim.cmd('silent! ' .. (s.history_row + 1) .. 'normal! zD')
    end
    if old and old.first then
      local first, last = position(self, old.first), position(self, old.last)
      if first and vim.fn.foldlevel(first) > 0 then
        closed = vim.fn.foldclosed(first) >= 0
        if valid and first == s.history_row + 1 and last == s.close_row
            and vim.fn.foldlevel(first - 1) == 0
            and vim.fn.foldlevel(last) == 1
            and vim.fn.foldlevel(last + 1) == 0 then return end
        vim.cmd('silent! ' .. first .. 'foldopen!')
        vim.cmd('silent! ' .. first .. 'normal! zd')
      end
      vim.api.nvim_buf_del_extmark(self.model.buf, self.ns, old.first)
      vim.api.nvim_buf_del_extmark(self.model.buf, self.ns, old.last)
    end
    state.cells[id] = { closed = closed }
    if valid then
      local first, last = s.history_row + 1, s.close_row
      vim.cmd(first .. ',' .. last .. 'fold')
      if not closed then vim.cmd(first .. 'foldopen!') end
      state.cells[id] = { closed = closed,
        first = vim.api.nvim_buf_set_extmark(self.model.buf, self.ns, first - 1, 0, { right_gravity = true }),
        last = vim.api.nvim_buf_set_extmark(self.model.buf, self.ns, last - 1, 0, { right_gravity = true }) }
    elseif not cell then state.cells[id] = nil end
    vim.fn.winrestview(view)
  end)
end
function History:refresh()
  if self.closed then return end
  local dirty = self.dirty; self.dirty = {}
  for id in pairs(dirty) do
    local deferred = self.deferred[id]
    if deferred then
      self.deferred[id] = nil
      for _, body in ipairs(deferred) do self:accept(id, body) end
    end
  end
  for _, win in ipairs(vim.fn.win_findbuf(self.model.buf)) do
    if not self.windows[win] or not self.windows[win].active then
      local saved = {}
      for _, name in ipairs({ 'foldmethod', 'foldtext', 'foldenable', 'foldminlines', 'fillchars', 'winhighlight' }) do saved[name] = vim.wo[win][name] end
      self.windows[win] = { saved = saved, active = true, cells = self.windows[win] and self.windows[win].cells or {} }
      local mappings = {}
      for mapping in saved.winhighlight:gmatch('[^,]+') do
        if not mapping:match('^Folded:') then table.insert(mappings, mapping) end
      end
      table.insert(mappings, 'Folded:Normal')
      vim.wo[win].winhighlight = table.concat(mappings, ',')
      vim.wo[win].foldmethod = 'manual'
      vim.wo[win].foldtext = "v:lua.require'jusi.history'.foldtext()"
      vim.wo[win].foldenable = true
      vim.wo[win].foldminlines = 0
      vim.api.nvim_win_call(win, function() vim.opt_local.fillchars:append({ fold = ' ' }) end)
      for _, cell in ipairs(self.model:ordered_cells()) do self:update(win, cell.id) end
    else
      for id in pairs(dirty) do self:update(win, id) end
    end
  end
end
function History:changed(ids)
  for _, id in ipairs(ids or {}) do
    self.dirty[id] = true
    if not self.model:cell_by_id(id) then
      self.deferred[id] = nil
      for trace, pending in pairs(self.pending) do if pending.id == id then self.pending[trace] = nil end end
    end
  end
  if self.closed or self.scheduled then return end
  self.scheduled = true
  vim.schedule(function() self.scheduled = false; self:refresh() end)
end
function History:toggle()
  self:refresh()
  local cell = self.model:cell_at_row(vim.api.nvim_win_get_cursor(0)[1] - 1)
  local s = cell and self.model:cell_snapshot(cell)
  if not s or not s.valid or not s.history_row or not s.close_row then return false end
  self:update(vim.api.nvim_get_current_win(), cell.id)
  local view = vim.fn.winsaveview()
  local line = s.history_row + 1
  if vim.fn.foldlevel(line) == 0 then return false end
  vim.cmd(line .. (vim.fn.foldclosed(line) >= 0 and 'foldopen!' or 'foldclose'))
  vim.fn.winrestview(view)
  return true
end
function History:apply(row)
  row = row or vim.api.nvim_win_get_cursor(0)[1] - 1
  local cell = self.model:cell_at_row(row)
  local s = cell and self.model:cell_snapshot(cell)
  if not s or not s.valid then return false end
  for _, entry in ipairs(s.history_entries) do
    if row >= entry.start_row - 1 and row < entry.end_row then
      local lines = vim.api.nvim_buf_get_lines(self.model.buf, entry.start_row, entry.end_row, false)
      local first = s.body_start_row
      local header = vim.api.nvim_buf_get_lines(self.model.buf, first, first + 1, false)[1] or ''
      if header:match('^%s*%%%%[%a][%w_-]*') then first = first + 1 end
      vim.api.nvim_buf_set_lines(self.model.buf, first, s.body_end_row, false, lines)
      self.model:flush(); self:changed({ cell.id }); self:refresh()
      s = self.model:cell_snapshot(cell)
      vim.cmd((s.history_row + 1) .. 'foldclose')
      vim.api.nvim_win_set_cursor(0, { math.min(first + 1, s.history_row + 1), 0 })
      return true
    end
  end
  return false
end
function History:before_edit(id)
  for win, state in pairs(self.windows) do
    local range = state.cells[id]
    if range and range.first and vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == self.model.buf then
      vim.api.nvim_win_call(win, function()
        local first = position(self, range.first)
        if first and vim.fn.foldlevel(first) > 0 then
          range.closed = vim.fn.foldclosed(first) >= 0
          local view = vim.fn.winsaveview()
          vim.cmd('silent! ' .. first .. 'normal! zd')
          vim.fn.winrestview(view)
        end
      end)
      vim.api.nvim_buf_del_extmark(self.model.buf, self.ns, range.first)
      vim.api.nvim_buf_del_extmark(self.model.buf, self.ns, range.last)
      range.first, range.last = nil, nil
    end
  end
end
function History:capture(id, body)
  if self.closed then return true end
  if not self.model:cell_by_id(id) then return true end
  local s = self.model:cell_snapshot(id)
  if not s or not s.valid or not vim.bo[self.model.buf].modifiable then return end
  local lines = vim.split(body, '\n', { plain = true })
  if (lines[1] or ''):match('^%s*%%%%[%a][%w_-]*') then table.remove(lines, 1) end
  if #lines == 0 then lines = { '' } end
  -- Literal reserved delimiter lines cannot be represented by the native grammar.
  for _, line in ipairs(lines) do
    if require('jusi.notebook.parser').line_kind(line) then return true end
  end
  local entries = self.model:history(id)
  local replacement = { '╞══' }
  vim.list_extend(replacement, lines)
  for _, entry in ipairs(entries) do
    if not vim.deep_equal(entry, lines) then
      table.insert(replacement, '├┄┄'); vim.list_extend(replacement, entry)
    end
  end
  if vim.deep_equal(replacement, vim.api.nvim_buf_get_lines(self.model.buf, s.history_row or s.close_row, s.close_row, false)) then return true end
  self:before_edit(id)
  vim.api.nvim_buf_set_lines(self.model.buf, s.history_row or s.close_row, s.close_row, false, replacement)
  self.model:flush(); self:changed({ id })
  return true
end
function History:accept(id, body)
  if not self:capture(id, body) then
    self.deferred[id] = self.deferred[id] or {}
    table.insert(self.deferred[id], body)
  end
end
function History:submit(command, id)
  if command.kind == 'execute' and not (command.code or ''):match('^%s*%%%%[%a][%w_-]*') then return end
  self.pending[command.trace_id] = { id = id, body = command.code or command.body, kind = command.kind }
end
function History:result(command, response, failure)
  local pending = self.pending[command.trace_id]
  if not pending then return end
  if command.kind == 'followup' and (response or failure and failure.layer == 'plugin_worker') then
    self:accept(pending.id, pending.body)
  end
  if command.kind == 'followup' and (not failure or failure.layer ~= 'frontend_transport') then self.pending[command.trace_id] = nil end
  if command.kind == 'execute' and failure and (failure.layer == 'supervisor' or failure.layer == 'protocol') then self.pending[command.trace_id] = nil end
end
function History:event(event)
  local pending = self.pending[event.trace_id]
  if not pending then return end
  if event.kind == 'client.created' and pending.kind == 'execute' then
    if vim.tbl_contains(event.payload.capabilities, 'followup') then self:accept(pending.id, pending.body) end
    self.pending[event.trace_id] = nil
  elseif event.kind == 'execution.completed' and pending.kind == 'execute' then
    self.pending[event.trace_id] = nil
  elseif pending.kind == 'followup' and (event.kind == 'operation.completed' and event.payload.outcome == 'succeeded'
      or event.kind == 'failure.occurred' and event.payload.layer == 'plugin_worker') then
    self:accept(pending.id, pending.body); self.pending[event.trace_id] = nil
  end
end
function History:leave(win)
  local state = self.windows[win]
  if not state or not state.active then return end
  for id in pairs(state.cells) do
    local range = state.cells[id]
    if range.first then
      local first = position(self, range.first)
      if first and vim.fn.foldlevel(first) > 0 then
        range.closed = vim.fn.foldclosed(first) >= 0
        vim.cmd('silent! ' .. first .. 'normal! zd')
      end
    end
  end
  for name, value in pairs(state.saved) do vim.wo[win][name] = value end
  state.active = false
end
function History:close()
  local views = {}
  for win, state in pairs(self.windows) do
    if vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == self.model.buf then
      views[tostring(win)] = {}
      vim.api.nvim_win_call(win, function()
        for id, range in pairs(state.cells) do
          local s = self.model:cell_snapshot(id)
          local first = range.first and position(self, range.first)
          if s then
            local closed = range.closed
            if first and state.active then closed = vim.fn.foldclosed(first) >= 0 end
            views[tostring(win)][tostring(s.open_row)] = closed
          end
        end
      end)
    end
  end
  if vim.api.nvim_buf_is_valid(self.model.buf) then vim.b[self.model.buf].jusi_history_views = views end
  self.closed = true; instances[self.model.buf] = nil
  vim.api.nvim_del_augroup_by_id(self.group)
  for win, state in pairs(self.windows) do
    if state.active and vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == self.model.buf then
      vim.api.nvim_win_call(win, function()
        for _, range in pairs(state.cells) do
          local first = range.first and position(self, range.first)
          if first then vim.cmd('silent! ' .. first .. 'normal! zd') end
        end
      end)
      for name, value in pairs(state.saved) do vim.wo[win][name] = value end
    end
  end
  if vim.api.nvim_buf_is_valid(self.model.buf) then vim.api.nvim_buf_clear_namespace(self.model.buf, self.ns, 0, -1) end
end
function M.new(model)
  highlights()
  local self = setmetatable({ model = model, windows = {}, dirty = {}, pending = {}, deferred = {}, restored = vim.b[model.buf].jusi_history_views or {},
    ns = vim.api.nvim_create_namespace('jusi_history_' .. model.notebook_id) }, History)
  instances[model.buf] = self
  vim.b[model.buf].jusi_history_views = nil
  self.group = vim.api.nvim_create_augroup('jusi_history_' .. model.notebook_id, { clear = true })
  vim.api.nvim_create_autocmd('ColorScheme', { group = self.group, callback = highlights })
  vim.api.nvim_create_autocmd('BufWinEnter', { group = self.group, buffer = model.buf, callback = function() self:changed() end })
  vim.api.nvim_create_autocmd('BufWinLeave', { group = self.group, buffer = model.buf, callback = function()
    local win = vim.api.nvim_get_current_win()
    if vim.api.nvim_win_get_buf(win) == model.buf then self:leave(win) end
  end })
  vim.api.nvim_create_autocmd('WinClosed', { group = self.group, callback = function(event)
    local win = tonumber(event.match)
    local state = self.windows[win]
    if state then
      for _, range in pairs(state.cells) do
        if range.first then vim.api.nvim_buf_del_extmark(model.buf, self.ns, range.first); vim.api.nvim_buf_del_extmark(model.buf, self.ns, range.last) end
      end
      self.windows[win] = nil
    end
  end })
  self:changed()
  return self
end
function M.command(action)
  local self = instances[vim.api.nvim_get_current_buf()]
  if not self or not self[action](self) then vim.notify('Jusi: no history ' .. (action == 'apply' and 'entry at cursor' or 'in this cell'), vim.log.levels.INFO) end
end
return M
