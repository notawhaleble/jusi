-- Attention is client-owned state. Visiting never approves or answers a request.
local M = {}
local managers, options, focused, started = {}, { bell = true }, nil, false
local queued, timer, last_bell = {}, false, -math.huge
local Manager = {}
Manager.__index = Manager
local function queue_key(manager, id) return tostring(manager.session.buf) .. ':' .. id end
local function redraw()
  require('jusi.statusline').redraw()
  require('jusi.attention_tabline').refresh()
end
local function terminal_bell()
  if #vim.api.nvim_list_uis() == 0 then return end
  local tty = io.open('/dev/tty', 'w')
  if tty then tty:write('\7'); tty:flush(); tty:close() end
end
local function client_buffer(session, client_id)
  for _, record in pairs(session.interactive.surfaces) do
    if record.client.client_id == client_id and record.buf and vim.api.nvim_buf_is_valid(record.buf) then return record.buf end
  end
end
local function label(manager, item)
  local buf = client_buffer(manager.session, item.client_id)
  local number = buf and vim.b[buf].jusi_output_number
  local name = vim.fn.fnamemodify(vim.api.nvim_buf_get_name(manager.session.buf), ':t')
  return (name ~= '' and name or 'notebook') .. ' / client ' .. (number or item.client_id)
end
local function announce(manager, item)
  queued[queue_key(manager, item.attention_id)] = { manager = manager, item = item }
  if timer then return end
  timer = true
  vim.defer_fn(function()
    timer = false
    local batch, lines, ring = {}, {}, false
    for _, entry in pairs(queued) do
      local current = entry.manager.items[entry.item.attention_id]
      if not entry.manager.closed and current and current.state == 'pending' then
        batch[#batch+1] = vim.deepcopy(current)
        if #lines < 5 then lines[#lines+1] = label(entry.manager, current) .. ': ' .. current.message end
        -- Focus reporting is not available in every terminal. Unknown focus
        -- uses a best-effort bell, while durable badges never depend on it.
        if focused ~= true then ring = true end
      end
    end
    queued = {}
    if #batch == 0 then return end
    table.sort(lines)
    if #batch > #lines then lines[#lines+1] = ('and %d more; :JusiAttention'):format(#batch-#lines) end
    vim.notify(table.concat(lines, '\n'), vim.log.levels.INFO, { title = 'Jusi attention' })
    local context = { focused = focused == true, focus_known = focused ~= nil }
    if options.notify then pcall(options.notify, batch, context) end
    local now = vim.uv.hrtime() / 1e9
    if ring and options.bell and now-last_bell >= 2 then
      last_bell = now
      pcall(type(options.bell) == 'function' and options.bell or terminal_bell)
    end
  end, 100)
end
function Manager:owns(item)
  local c = self.session.controller
  local client = c.clients[item.client_id]
  return item.editor_id == c.editor_id and item.notebook_id == self.session.model.notebook_id
    and client and client.runtime_id == item.runtime_id and client.cell_id == item.cell_id
    and self.session.model:cell_by_id(item.cell_id) ~= nil
end
function Manager:receive(item)
  if self.closed then return end
  if item.state == 'pending' and item.editor_id == self.session.controller.editor_id
      and not self:owns(item) then return end
  local revision = self.seen[item.attention_id] or 0
  if item.revision <= revision then return end
  self.seen[item.attention_id] = item.revision
  if item.state == 'cleared' or not self:owns(item) then
    self.items[item.attention_id] = nil
    queued[queue_key(self, item.attention_id)] = nil
    self.completed[#self.completed+1] = item.attention_id
    while #self.completed > 128 do
      local id = table.remove(self.completed, 1)
      if not self.items[id] then self.seen[id], self.announced[id] = nil, nil end
    end
  else
    self.items[item.attention_id] = vim.deepcopy(item)
    if not self.announced[item.attention_id] then
      self.announced[item.attention_id] = true
      announce(self, item)
    end
  end
  redraw()
end
function Manager:resync(items)
  local present = {}
  for _, item in ipairs(items) do present[item.attention_id] = true; self:receive(item) end
  for id in pairs(self.items) do
    if not present[id] then self.items[id] = nil; queued[queue_key(self, id)] = nil end
  end
  redraw()
end
function Manager:retire(client_id)
  for id, item in pairs(self.items) do
    if item.client_id == client_id then self.items[id] = nil; queued[queue_key(self, id)] = nil end
  end
  redraw()
end
function Manager:dismiss(item)
  local c = self.session.controller
  if self.closed or item.kind ~= 'notice' or self.items[item.attention_id] ~= item
      or c.transport_state ~= 'connected' or self.dismissing[item.attention_id] then return end
  self.dismissing[item.attention_id] = true
  c:_request('POST', '/v1/attention/' .. item.attention_id, c:_command('dismiss_attention', {
    attention_id = item.attention_id, editor_id = c.editor_id, revision = item.revision,
  }), function(response, failure)
    self.dismissing[item.attention_id] = nil
    if self.closed then return end
    if not failure and require('jusi.protocol').validate_attention_ack(response)
        and response.attention_id == item.attention_id and self.items[item.attention_id] == item then
      self.items[item.attention_id] = nil
      queued[queue_key(self, item.attention_id)] = nil
      redraw()
    end
  end)
end
function Manager:visit(buf)
  if focused == false then return end
  for _, item in pairs(self.items) do
    if client_buffer(self.session, item.client_id) == buf then self:dismiss(item) end
  end
end
function Manager:close()
  self.closed = true
  for id in pairs(self.items) do queued[queue_key(self, id)] = nil end
  managers[self.session.buf] = nil
  self.items, self.seen, self.announced = {}, {}, {}
  redraw()
end
function M.attach(session)
  if managers[session.buf] then managers[session.buf]:close() end
  local self = setmetatable({ session = session, items = {}, seen = {}, announced = {}, completed = {}, dismissing = {} }, Manager)
  managers[session.buf] = self
  local c = session.controller
  c.on_attention = function(item) self:receive(item) end
  c.on_attention_snapshot = function(items) self:resync(items) end
  c.on_attention_closed = function(id) self:retire(id) end
  return self
end
function M.count(buf, client_id)
  local manager, count = managers[buf], 0
  if manager then for _, item in pairs(manager.items) do if not client_id or item.client_id == client_id then count = count + 1 end end end
  return count
end
-- A visible client determines location. Notebook views are only a fallback
-- when the client is hidden, so mirrored notebooks do not claim another tab's UI.
function M.tab_summaries()
  local result = {}
  local function tabs_for(buf)
    local tabs = {}
    if buf and vim.api.nvim_buf_is_valid(buf) then
      for _, win in ipairs(vim.fn.win_findbuf(buf)) do
        if vim.api.nvim_win_is_valid(win) then tabs[vim.api.nvim_win_get_tabpage(win)] = true end
      end
    end
    return tabs
  end
  for _, manager in pairs(managers) do
    for _, item in pairs(manager.items) do
      local tabs = tabs_for(client_buffer(manager.session, item.client_id))
      if not next(tabs) then tabs = tabs_for(manager.session.buf) end
      for tab in pairs(tabs) do
        local summary = result[tab] or { count = 0, action_required = 0, notice = 0 }
        summary.count = summary.count + 1
        summary[item.kind] = summary[item.kind] + 1
        result[tab] = summary
      end
    end
  end
  return result
end
function M.show(dismiss)
  local entries, grouped = {}, {}
  for _, manager in pairs(managers) do
    for _, item in pairs(manager.items) do
      if not dismiss or item.kind == 'notice' then
        local key = queue_key(manager, item.client_id)
        if not grouped[key] then
          local entry = { manager = manager, client_id = item.client_id, item = item }
          grouped[key] = entry; entries[#entries+1] = entry
        end
      end
    end
  end
  table.sort(entries, function(a,b) return label(a.manager,a.item) < label(b.manager,b.item) end)
  local function select(entry)
    if not entry or entry.manager.closed then return end
    local manager = entry.manager
    local pending = false
    for _, item in pairs(manager.items) do
      if item.client_id == entry.client_id and (not dismiss or item.kind == 'notice') then pending = true end
    end
    if not pending then return end -- The item may clear while a picker is open.
    if dismiss then
      for _, item in pairs(manager.items) do if item.client_id == entry.client_id then manager:dismiss(item) end end
      return
    end
    local buf = client_buffer(manager.session, entry.client_id)
    if not buf then vim.notify('Jusi attention: client surface is unavailable'); return end
    local window = require('jusi.presentation.window')
    local win = window.find(buf)
    if win then vim.api.nvim_set_current_win(win)
    else window.show(buf, { tab = vim.api.nvim_get_current_tabpage(), enter = true, height = 12 }) end
    manager:visit(buf)
    vim.cmd.startinsert()
  end
  if #entries == 0 then vim.notify(dismiss and 'No Jusi notices to dismiss' or 'No pending Jusi attention')
  elseif #entries == 1 then select(entries[1])
  else vim.ui.select(entries, { prompt = dismiss and 'Dismiss Jusi notices' or 'Jusi attention',
    format_item = function(entry) return label(entry.manager, entry.item) .. ': ' .. entry.item.message end }, select) end
end
function M.setup(opts)
  opts = opts or {}
  assert(type(opts) == 'table', 'attention must be an options table')
  for key in pairs(opts) do assert(key == 'bell' or key == 'notify' or key == 'tabline', 'unknown attention option: ' .. key) end
  require('jusi.attention_tabline').setup(opts.tabline)
  if opts.bell ~= nil then
    assert(type(opts.bell) == 'boolean' or type(opts.bell) == 'function', 'attention.bell must be boolean or function')
    options.bell = opts.bell
  end
  if opts.notify ~= nil then
    assert(type(opts.notify) == 'function' or opts.notify == false, 'attention.notify must be a function or false')
    options.notify = opts.notify
  end
  if started then return end
  started = true
  local group = vim.api.nvim_create_augroup('jusi_attention', { clear = true })
  local function visit()
    local buf = vim.api.nvim_get_current_buf()
    for _, manager in pairs(managers) do manager:visit(buf) end
  end
  vim.api.nvim_create_autocmd('FocusLost', { group = group, callback = function() focused = false end })
  vim.api.nvim_create_autocmd('FocusGained', { group = group, callback = function() focused = true; visit() end })
  vim.api.nvim_create_autocmd({ 'BufEnter', 'WinEnter', 'TermEnter' }, { group = group, callback = visit })
end
return M
