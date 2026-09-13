local M = {}
local windows = require('jusi.presentation.window')

local function escape(value)
  return (value:gsub('([\\%s])', '\\%1'))
end

function M.notebooks()
  local sessions = require('jusi')._sessions
  local result, counts = {}, {}
  local current = vim.api.nvim_get_current_buf()
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_loaded(buf) and (sessions[buf] or vim.bo[buf].filetype == 'jusi') then
      local path = vim.api.nvim_buf_get_name(buf)
      local label = path ~= '' and vim.fn.fnamemodify(path, ':t:r') or 'notebook-' .. buf
      counts[label] = (counts[label] or 0) + 1
      table.insert(result, { buf = buf, label = label, path = path, session = sessions[buf] })
    end
  end
  local used = {}
  for _, item in ipairs(result) do
    if counts[item.label] > 1 then item.label = vim.fn.fnamemodify(item.path, ':~:.:r') end
    if used[item.label] then item.label = item.label .. '#' .. item.buf end
    used[item.label] = true
  end
  table.sort(result, function(a, b)
    if a.buf == b.buf then return false end
    if a.buf == current then return true end
    if b.buf == current then return false end
    return a.buf < b.buf
  end)
  return result
end

local function find(label)
  for _, item in ipairs(M.notebooks()) do if item.label == label then return item end end
end

local function palette(item)
  local runtime = item and item.session and item.session.controller
  if not runtime or not runtime.plugin_catalog then return {} end
  if runtime.palette then return runtime.palette end
  local result = {}
  for _, plugin in ipairs(runtime.plugin_catalog.plugins) do
    for _, family in ipairs(plugin.families) do result[family.magic_name] = { entries = {} } end
  end
  return result
end

-- Ex arguments use backslash escaping, including notebook names with spaces.
local function words(line)
  local result, word, escaped, active = {}, '', false, false
  for i = 1, #line do
    local c = line:sub(i, i)
    if escaped then word, escaped, active = word .. c, false, true
    elseif c == '\\' then escaped, active = true, true
    elseif c:match('%s') then
      if active then table.insert(result, word); word, active = '', false end
    else word, active = word .. c, true end
  end
  if escaped then word = word .. '\\' end
  table.insert(result, word)
  return result
end

function M.complete(_, line, position)
  local args = words(line:sub(1, position))
  table.remove(args, 1) -- J or J!, including an optional Ex range.
  local candidates = {}
  if #args == 1 then
    for _, item in ipairs(M.notebooks()) do table.insert(candidates, item.label) end
  elseif #args == 2 then
    candidates = vim.tbl_keys(palette(find(args[1]))); table.sort(candidates)
  elseif #args == 3 then
    local section = palette(find(args[1]))[args[2]]
    candidates = section and section.entries or {}
  end
  local result, prefix = {}, args[#args] or ''
  for _, candidate in ipairs(candidates) do
    if candidate:sub(1, #prefix) == prefix then table.insert(result, escape(candidate)) end
  end
  return result
end

local function selection(command)
  if command.range == 0 then return nil end
  local first, last = vim.fn.getpos("'<"), vim.fn.getpos("'>")
  if first[2] == command.line1 and last[2] == command.line2 and vim.fn.visualmode() ~= '' then
    return vim.fn.getregion(first, last, { type = vim.fn.visualmode(), exclusive = vim.o.selection == 'exclusive' })
  end
  return vim.api.nvim_buf_get_lines(0, command.line1 - 1, command.line2, false)
end

function M.command(command)
  local args = command.fargs
  local item = find(args[1])
  if not item then error('Jusi: unknown notebook; use :J <Tab>') end
  if not vim.bo[item.buf].modifiable then error('Jusi: notebook is not modifiable') end
  local header
  if args[2] then
    local section = palette(item)[args[2]]
    if not section then error('Jusi: unknown magic: ' .. args[2]) end
    if #section.entries > 0 and not vim.tbl_contains(section.entries, args[3]) then
      error('Jusi: select a configured ' .. args[2] .. ' entry')
    end
    header = '%%' .. table.concat(args, ' ', 2)
  end
  local body = selection(command) -- capture before changing the current window
  local editor = require('jusi.editing').attach(item.buf)
  local model, snapshot = editor.model
  if header then
    for _, cell in ipairs(model:ordered_cells()) do
      local candidate = model:cell_snapshot(cell)
      local actual = candidate and vim.api.nvim_buf_get_lines(item.buf, candidate.body_start_row, candidate.body_start_row + 1, false)[1] or ''
      if candidate and candidate.valid and (actual == header or actual:sub(1, #header + 1) == header .. ' ') then
        snapshot = candidate; break
      end
    end
  end
  local created = not snapshot
  local row
  if snapshot then
    row = snapshot.body_start_row
    if body then
      local lines = { header }; vim.list_extend(lines, body)
      vim.api.nvim_buf_set_lines(item.buf, row, snapshot.body_end_row, false, lines)
    else
      vim.api.nvim_buf_set_lines(item.buf, row, row + 1, false, { header })
    end
  else
    local lines = { '╭──' }
    if header then table.insert(lines, header) end
    vim.list_extend(lines, body and #body > 0 and body or { '' })
    table.insert(lines, '╰──')
    local count = vim.api.nvim_buf_line_count(item.buf)
    local empty = count == 1 and vim.api.nvim_buf_get_lines(item.buf, 0, 1, false)[1] == ''
    local start = empty and 0 or count
    vim.api.nvim_buf_set_lines(item.buf, start, count, false, lines)
    row = start + 1
  end
  model:flush()
  local win = windows.show(item.buf, { tab = vim.api.nvim_get_current_tabpage(), split = 'left', enter = true })
  -- Keep the magic header visible but put typing on its body, creating a body
  -- row when a reused cell has only a header before its history/closer.
  if header then row = row + 1 end
  local cell = model:cell_at_row(row - (header and 1 or 0))
  local current = cell and model:cell_snapshot(cell)
  if current and row >= current.body_end_row then
    vim.api.nvim_buf_set_lines(item.buf, row, row, false, { '' }); model:flush()
  end
  vim.api.nvim_win_set_cursor(win, { row + 1, 0 })
  vim.cmd('silent! normal! zv')
  if command.bang then return require('jusi').submit(item.buf, row) end
  if created then vim.cmd.startinsert() end
  return cell
end
return M
