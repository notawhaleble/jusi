local M = {}
local Editing = {}
Editing.__index = Editing
local instances = {}
local function signature(line)
  local magic = line:match("^%s*%%%%([%a][%w_-]*)")
  return magic, magic and line or ""
end
local function options(buf)
  local result = {}
  for _, name in ipairs({ "shiftwidth", "tabstop", "softtabstop", "expandtab" }) do result[name] = vim.bo[buf][name] end
  return result
end
function Editing:request(request)
  if self.closed or not self.job then return nil end
  local ok, result = pcall(vim.rpcrequest, self.job, "nvim_exec_lua", "return require('jusi.editing_worker').run(...)", { request })
  if not ok then
    if not self.warned.worker then vim.notify("Jusi cell editing: " .. tostring(result), vim.log.levels.WARN); self.warned.worker = true end
    return nil
  end
  return result
end
function Editing:context(cell)
  local snapshot = self.model:cell_snapshot(cell)
  if not snapshot then return nil end
  local lines = vim.api.nvim_buf_get_lines(self.model.buf, snapshot.body_start_row, snapshot.body_end_row, false)
  local magic, header = signature(lines[1] or "")
  local profile = { syntax = "python", indent = "python" }
  local family = magic and self.families[magic]
  if family then profile = vim.tbl_extend("force", profile, family) end
  local override = self.overrides[cell.id]
  if override and (override.header ~= header or override.header_revision ~= cell.header_revision) then self.overrides[cell.id] = nil; override = nil end
  if override then profile = vim.tbl_extend("force", profile, override.profile) end
  local start = snapshot.body_start_row
  if magic then table.remove(lines, 1); start = start + 1 end
  local regions = {}
  if snapshot.valid then
    for index, entry in ipairs(snapshot.history_entries) do
      table.insert(regions, { id = cell.id .. ":history:" .. index,
        start = entry.start_row, finish = entry.end_row,
        lines = vim.api.nvim_buf_get_lines(self.model.buf, entry.start_row, entry.end_row, false) })
    end
  end
  return { regions = regions, id = cell.id, syntax = profile.syntax, indent = profile.indent, lines = lines,
    options = options(self.model.buf), start = start, finish = snapshot.body_end_row,
    header = header, header_revision = cell.header_revision, magic = magic, open = snapshot.open_row, revision = cell.text_revision }
end
function Editing:catalog()
  self.families, self.providers = {}, {}
  local catalog = self.controller.plugin_catalog
  for _, plugin in ipairs(catalog and catalog.plugins or {}) do
    self.providers[plugin.plugin_id] = {}
    for _, family in ipairs(plugin.families) do
      self.families[family.magic_name] = family.presentation or {}
      self.providers[plugin.plugin_id][family.family_id] = family.provider_presentation or {}
    end
  end
  for _, client in pairs(self.controller.clients or {}) do self:client(client) end
end
function Editing:event(event)
  self.history:event(event)
  if event.kind == "execution.started" then
    local cell = self.model:cell_by_id(event.payload.cell_id)
    local ctx = cell and self:context(cell)
    if ctx then
      for id, submitted in pairs(self.submissions) do if submitted.id == cell.id then self.submissions[id] = nil end end
      self.submissions[event.payload.execution_id] = { id = cell.id, header = ctx.header, header_revision = ctx.header_revision }
    end
  elseif event.kind == "client.created" then
    self:client(event.payload)
  end
end
function Editing:client(client)
  local cell = self.model:cell_by_id(client.cell_id)
  local ctx = cell and self:context(cell)
  local submitted = self.submissions[client.execution_id]
  if not ctx or not submitted or (submitted.header ~= ctx.header or submitted.header_revision ~= ctx.header_revision) then return end
  local provider = self.providers[client.plugin_id]
  local profile = provider and provider[client.family_id]
  if profile then
    self.overrides[cell.id] = { header = ctx.header, header_revision = ctx.header_revision, profile = profile }
    self.cache[cell.id] = nil
    self:schedule()
  end
end
function Editing:retire(id)
  self.history:changed({ id })
  self.overrides[id], self.cache[id] = nil, nil
  local ns = self.namespaces[id]
  if ns and vim.api.nvim_buf_is_valid(self.model.buf) then vim.api.nvim_buf_clear_namespace(self.model.buf, ns, 0, -1) end
  self.namespaces[id] = nil
  self:request({ action = "retire", id = id })
  for execution, submitted in pairs(self.submissions) do if submitted.id == id then self.submissions[execution] = nil end end
end
function Editing:visible_cells()
  local result = {}
  for _, win in ipairs(vim.fn.win_findbuf(self.model.buf)) do
    local first, last = unpack(vim.api.nvim_win_call(win, function() return { vim.fn.line("w0") - 1, vim.fn.line("w$") - 1 } end))
    local cell = self.model:cell_at_row(first)
    if not cell then cell = self.model:_next_open_at_or_after(first) end
    while cell do
      local snapshot = self.model:cell_snapshot(cell)
      if not snapshot or snapshot.open_row > last then break end
      result[cell.id] = cell
      cell = cell.next
    end
  end
  return result
end
function Editing:refresh()
  if self.closed then return end
  local visible = self:visible_cells()
  for id in pairs(self.cache) do
    if not visible[id] then
      if self.namespaces[id] then vim.api.nvim_buf_clear_namespace(self.model.buf, self.namespaces[id], 0, -1) end
      self.cache[id] = nil
      self:request({ action = "retire", id = id })
    end
  end
  for id, cell in pairs(visible) do
    local ctx = self:context(cell)
    if ctx then
      local region_keys = {}
      for _, region in ipairs(ctx.regions) do table.insert(region_keys, { region.lines, region.start - ctx.start }) end
      local key = vim.inspect({ ctx.lines, region_keys, ctx.syntax, ctx.indent, ctx.options, ctx.header, ctx.revision })
      if self.cache[id] ~= key then
        ctx.action = "highlight"
        local tick = vim.api.nvim_buf_get_changedtick(self.model.buf)
        local result = self:request(ctx)
        if self.closed then return end
        if tick ~= vim.api.nvim_buf_get_changedtick(self.model.buf) then self:schedule(); return end
        if result then
          for key in (result.indentkeys or ""):gmatch("[^,]+") do self.indentkeys[key] = true end
          local keys = vim.tbl_keys(self.indentkeys); table.sort(keys)
          vim.bo[self.model.buf].indentkeys = table.concat(keys, ",")
          local ns = self.namespaces[id] or vim.api.nvim_create_namespace("jusi_editing_" .. id)
          self.namespaces[id] = ns
          vim.api.nvim_buf_clear_namespace(self.model.buf, ns, 0, -1)
          for _, span in ipairs(result.spans) do
            vim.api.nvim_buf_set_extmark(self.model.buf, ns, ctx.start + span[1], span[2], {
              end_col = span[3], hl_group = span[4], priority = 100,
            })
          end
          if ctx.magic then vim.api.nvim_buf_set_extmark(self.model.buf, ns, ctx.start - 1, 0, {
            end_col = #ctx.header, hl_group = "PreProc", priority = 100,
          }) end
          self.cache[id] = key
          for _, kind in ipairs({ "syntax", "indent" }) do
            local name = ctx[kind]
            if not result[kind .. "_found"] and not self.warned[kind .. name] then
              self.warned[kind .. name] = true
              vim.notify("Jusi: unavailable " .. kind .. " profile " .. name, vim.log.levels.WARN)
            end
          end
        end
      end
    end
  end
end
function Editing:schedule()
  if self.closed or self.scheduled then return end
  self.scheduled = true
  vim.schedule(function() self.scheduled = false; self:refresh() end)
end
function Editing:changed(ids, structural, history_ids)
  if structural then self.history:changed(ids)
  elseif history_ids and #history_ids > 0 then self.history:changed(history_ids) end
  -- Invalidate provider attribution even if a magic is edited away and back
  -- while the cell is offscreen.
  for _, id in ipairs(ids) do
    if structural then self.cache[id] = nil end
    local cell = self.model:cell_by_id(id)
    if cell then self:context(cell) end
  end
  self:schedule()
end
function Editing:indent(lnum)
  local cell = self.model:cell_at_row(lnum - 1)
  local ctx = cell and self:context(cell)
  if ctx then
    for _, region in ipairs(ctx.regions) do
      if lnum - 1 >= region.start and lnum - 1 < region.finish then
        ctx.id, ctx.lines, ctx.start, ctx.finish = region.id, region.lines, region.start, region.finish
        break
      end
    end
  end
  if not ctx or lnum - 1 < ctx.start or lnum - 1 >= ctx.finish then return 0 end
  ctx.action, ctx.lnum = "indent", lnum - ctx.start
  return self:request(ctx) or -1
end
function M.indent()
  local editing = instances[vim.api.nvim_get_current_buf()]
  return editing and editing:indent(vim.v.lnum) or 0
end
function Editing:close()
  if self.closed then return end
  self.closed = true
  self.cellmode:close()
  if self.detach_focus then self.detach_focus() end
  if self.detach_mappings then self.detach_mappings() end
  if self.offline_marks then self.offline_marks:close() end
  self.history:close()
  instances[self.model.buf] = nil
  vim.api.nvim_del_augroup_by_id(self.group)
  if self.job then pcall(vim.fn.jobstop, self.job) end
  if self.owns_model then self.model:detach() end
  if vim.api.nvim_buf_is_valid(self.model.buf) then
    for _, ns in pairs(self.namespaces) do vim.api.nvim_buf_clear_namespace(self.model.buf, ns, 0, -1) end
    for name, value in pairs(self.saved) do vim.bo[self.model.buf][name] = value end
  end
end
function M.new(model, controller)
  local self = setmetatable({ model = model, controller = controller, families = {}, providers = {}, overrides = {},
    submissions = {}, namespaces = {}, cache = {}, warned = {}, saved = {}, indentkeys = {} }, Editing)
  self.history = require("jusi.history").new(model)
  self.job = vim.fn.jobstart({ vim.v.progpath, "--headless", "--embed", "-u", "NONE", "-i", "NONE", "-n" }, { rpc = true })
  assert(self.job > 0, "could not start local cell editing worker")
  vim.rpcrequest(self.job, "nvim_set_option_value", "runtimepath", vim.o.runtimepath, {})
  self.group = vim.api.nvim_create_augroup("jusi_editing_" .. model.notebook_id, { clear = true })
  for _, name in ipairs({ "indentexpr", "indentkeys", "autoindent", "shiftwidth", "softtabstop", "expandtab" }) do self.saved[name] = vim.bo[model.buf][name] end
  vim.bo[model.buf].indentexpr = "v:lua.require'jusi.editing'.indent()"
  vim.bo[model.buf].indentkeys = "0{,0},0),0],:,0#,!^F,o,O,e,=elif,=except,=else,=end"
  for key in vim.bo[model.buf].indentkeys:gmatch("[^,]+") do self.indentkeys[key] = true end
  vim.bo[model.buf].autoindent = true
  vim.bo[model.buf].shiftwidth, vim.bo[model.buf].softtabstop, vim.bo[model.buf].expandtab = 4, 4, true
  instances[model.buf] = self
  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI", "TextChangedP", "BufWinEnter", "InsertEnter" }, {
    group = self.group, buffer = model.buf, callback = function() self:schedule() end,
  })
  vim.api.nvim_create_autocmd({ "WinScrolled", "WinResized", "WinClosed" }, { group = self.group, callback = function() self:schedule() end })
  vim.api.nvim_create_autocmd("ColorScheme", { group = self.group, callback = function() self.cache = {}; self:schedule() end })
  vim.api.nvim_create_autocmd("BufWipeout", { group = self.group, buffer = model.buf, once = true,
    callback = function() self:close() end })
  self.cellmode = require("jusi.cellmode").new(self)
  self.detach_focus = require("jusi.focus").attach(model.buf)
  self.detach_mappings = require("jusi.mappings").attach(model.buf)
  require("jusi.statusline").setup()
  for _, win in ipairs(vim.fn.win_findbuf(model.buf)) do require("jusi.statusline").refresh(win) end
  self:catalog()
  self:schedule()
  return self
end
function M.detach_standalone(buf)
  local editing = instances[buf]
  if editing and editing.owns_model then editing:close() end
end
function M.attach(buf)
  buf = buf == 0 and vim.api.nvim_get_current_buf() or buf
  if instances[buf] then return instances[buf] end
  local model = require("jusi.notebook").attach(buf)
  local editing = M.new(model, {})
  editing.owns_model = true
  editing.offline_marks = require("jusi.marks").new(model, { clients = {} })
  model.on_text_changed = function(ids, history_ids) editing:changed(ids, false, history_ids) end
  model.on_cells_changed = function(ids)
    editing.offline_marks.on_cells_changed(ids)
    editing:changed(ids, true)
  end
  model.on_cells_retired = function(ids) for _, id in ipairs(ids) do editing.offline_marks:retire(id); editing:retire(id) end end
  return editing
end
return M
