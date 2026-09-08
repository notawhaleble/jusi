local protocol = require("jusi.protocol")
local M = {}
local active = {}
local pending = {}

local function chars(text) return vim.fn.strchars(text) end
local function slice(text, first, last)
  return vim.fn.strcharpart(text, first, last and (last - first) or chars(text))
end
local function lines(text) return vim.split(text, "\n", { plain = true }) end
local function position(text, offset)
  local prefix = slice(text, 0, offset)
  local parts = lines(prefix)
  return #parts - 1, #parts[#parts]
end
local function same_cursor(win, cursor)
  return vim.api.nvim_win_is_valid(win) and vim.deep_equal(vim.api.nvim_win_get_cursor(win), cursor)
end
local function target(controller, cell_id)
  local found
  for id, client in pairs(controller.clients) do
    if client.cell_id == cell_id and client.notebook_id == controller.notebook.notebook_id then
      if found then return nil, "cell has multiple clients" end
      found = id
    end
  end
  return found
end

local function plugin_magic(controller, first_line)
  local magic = first_line:match("^%s*%%%%([^%s]+)")
  if not magic then return false end
  local catalog = controller.plugin_catalog
  for _, plugin in ipairs(catalog and catalog.plugins or {}) do
    for _, family in ipairs(plugin.families) do
      if family.magic_name == magic then return true end
    end
  end
  return false
end

function M.capture(model, controller)
  local buf, win = model.buf, vim.api.nvim_get_current_win()
  if vim.api.nvim_get_current_buf() ~= buf then return nil, "complete from the source cell" end
  local cursor = vim.api.nvim_win_get_cursor(win)
  local cell = model:cell_at_row(cursor[1] - 1)
  if not cell then return nil, "cursor is not inside a cell" end
  local snapshot = model:cell_snapshot(cell)
  local body, err = model:body(cell.id)
  if not body then return nil, err end
  local row = cursor[1] - 1 - snapshot.body_start_row
  if row < 0 or row >= #body then return nil, "cursor must be in the cell body" end
  local column = math.min(cursor[2], #body[row + 1])
  local before = table.concat(body, "\n", 1, row)
  if row > 0 then before = before .. "\n" end
  before = before .. body[row + 1]:sub(1, column)
  local client_id, target_error = target(controller, cell.id)
  if target_error then return nil, target_error end
  if client_id and not vim.tbl_contains(controller.clients[client_id].capabilities, "complete") then
    return nil, "client does not support completion"
  end
  if not client_id and plugin_magic(controller, body[1] or "") then
    return nil, "execute the plugin cell to create a client before completing it"
  end
  if controller.kernel_state ~= "on" or not controller.kernel_id then return nil, "no authoritative live kernel" end
  return { buf = buf, win = win, cell_id = cell.id, model = model, controller = controller,
    client_id = client_id, kernel_id = controller.kernel_id, runtime_id = controller.runtime_id,
    supervisor_id = controller.supervisor_id, notebook_id = model.notebook_id,
    body = table.concat(body, "\n"), cursor_pos = chars(before), cursor = cursor,
    mode = vim.fn.mode():sub(1, 1), tick = vim.api.nvim_buf_get_changedtick(buf),
    start_row = snapshot.body_start_row }
end

local function identities(ctx)
  return ctx.controller.notebook == ctx.model and ctx.model.notebook_id == ctx.notebook_id
    and ctx.controller.kernel_id == ctx.kernel_id and ctx.controller.runtime_id == ctx.runtime_id
    and ctx.controller.supervisor_id == ctx.supervisor_id and ctx.controller.kernel_state == "on"
    and ctx.model:cell_by_id(ctx.cell_id) ~= nil and target(ctx.controller, ctx.cell_id) == ctx.client_id
end
local function unchanged(ctx)
  return vim.api.nvim_buf_is_valid(ctx.buf) and vim.api.nvim_get_current_buf() == ctx.buf
    and vim.api.nvim_get_current_win() == ctx.win and identities(ctx)
    and vim.api.nvim_buf_get_changedtick(ctx.buf) == ctx.tick and same_cursor(ctx.win, ctx.cursor)
end

local function finish(menu)
  if active[menu.ctx.buf] ~= menu then return end
  active[menu.ctx.buf] = nil
  if menu.group then vim.api.nvim_del_augroup_by_id(menu.group) end
end

function M.show(ctx, result)
  if not unchanged(ctx) or vim.fn.mode():sub(1, 1) ~= "i" then return false end
  local valid, err = protocol.validate_completion(result, ctx.cursor_pos)
  if not valid then return nil, err end
  if #result.items == 0 then return false end
  local prefix = slice(ctx.body, 0, ctx.cursor_pos)
  local line_start = ctx.cursor_pos - chars(lines(prefix)[#lines(prefix)])
  local first = ctx.cursor_pos
  for _, item in ipairs(result.items) do first = math.min(first, math.max(line_start, item.start)) end
  local start_col = #slice(ctx.body, line_start, first)
  local menu = { ctx = ctx, items = result.items, edits = {} }
  local matches = {}
  for index, item in ipairs(result.items) do
    local desired = slice(ctx.body, 0, item.start) .. item.text .. slice(ctx.body, item["end"], ctx.cursor_pos)
    local earlier = item.start < line_start
    local word = earlier and lines(desired)[#lines(desired)] or slice(desired, first)
    menu.edits[index] = { prefix = desired, word = word, earlier = earlier,
      cursor = item.start + chars(item.text) }
    matches[index] = { word = word, abbr = item.label or item.text:gsub("[\r\n]", " "),
      menu = item.detail or "", info = item.documentation or "", kind = item.kind or "",
      dup = 1, empty = 1, user_data = { jusi_completion = index } }
  end
  active[ctx.buf] = menu
  menu.group = vim.api.nvim_create_augroup("jusi_completion_" .. ctx.buf, { clear = true })
  vim.api.nvim_create_autocmd("CompleteDone", {
    group = menu.group, buffer = ctx.buf, once = true, callback = function()
      local selected = vim.v.completed_item
      local data = selected and selected.user_data
      local edit = type(data) == "table" and menu.edits[data.jusi_completion] or nil
      finish(menu)
      if not edit or vim.v.event.reason == "cancel" or not identities(ctx)
        or vim.api.nvim_get_current_buf() ~= ctx.buf or vim.api.nvim_get_current_win() ~= ctx.win then return end
      -- Native completion owns the same-line edit and all input semantics.
      -- Like an additional text edit, a range reaching into earlier lines is
      -- finalized only after acceptance. Never rewrite the suffix.
      local native_prefix = slice(ctx.body, 0, first) .. edit.word
      local end_row, end_col = position(native_prefix, chars(native_prefix))
      local ok, actual = pcall(vim.api.nvim_buf_get_text, ctx.buf, ctx.start_row, 0, ctx.start_row + end_row, end_col, {})
      if not ok or table.concat(actual, "\n") ~= native_prefix then return end
      if edit.earlier then
        pcall(vim.cmd, "undojoin")
        vim.api.nvim_buf_set_text(ctx.buf, ctx.start_row, 0, ctx.start_row + end_row, end_col, lines(edit.prefix))
      end
      if edit.earlier or edit.cursor ~= chars(edit.prefix) then
        local row, col = position(edit.prefix, edit.cursor)
        vim.api.nvim_win_set_cursor(ctx.win, { ctx.start_row + row + 1, col })
      end
    end,
  })
  vim.api.nvim_create_autocmd("BufWipeout", {
    group = menu.group, buffer = ctx.buf, once = true, callback = function() finish(menu) end,
  })
  vim.fn.complete(start_col + 1, matches)
  return true
end

function M.request(model, controller, callback)
  if vim.fn.pumvisible() == 1 then return end
  if vim.fn.mode():sub(1, 1) ~= "i" then
    callback(nil, "invoke completion in Insert mode")
    return
  end
  local ctx, err = M.capture(model, controller)
  if not ctx then callback(nil, err); return end
  pending[ctx.buf] = ctx
  return controller:complete(ctx, function(response, failure)
    if pending[ctx.buf] ~= ctx then return end
    pending[ctx.buf] = nil
    if not unchanged(ctx) then return end
    if failure then callback(nil, failure); return end
    local shown, show_error = M.show(ctx, response.completion)
    callback(shown, show_error)
  end)
end
M._active = active
return M
