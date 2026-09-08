-- Runs only in a notebook-owned headless Neovim. Language runtime scripts see
-- an isolated cell buffer, never neighboring cells or the user's windows.
local M = {}
local cells = {}
local function available(kind, name)
  return name ~= "" and #vim.api.nvim_get_runtime_file(kind .. "/" .. name .. ".vim", false) > 0
end
local function prepare(request)
  for _, name in ipairs({ request.syntax, request.indent }) do
    assert(type(name) == "string" and (#name == 0 or #name <= 64 and name:match("^[a-z][a-z0-9_]*$")), "invalid local editing profile")
  end
  local key = request.syntax .. ":" .. request.indent
  local cell = cells[request.id]
  local children = cell and cell.children
  if cell and cell.key ~= key then vim.api.nvim_buf_delete(cell.buf, { force = true }); cell = nil end
  if not cell then
    cell = { buf = vim.api.nvim_create_buf(false, true), key = key, children = children }
    cells[request.id] = cell
    vim.bo[cell.buf].bufhidden = "hide"
    vim.bo[cell.buf].filetype = request.syntax ~= "" and request.syntax or request.indent
    vim.api.nvim_buf_call(cell.buf, function()
      if available("syntax", request.syntax) then
        vim.cmd("runtime! syntax/" .. request.syntax .. ".vim")
        vim.cmd("syntax sync fromstart")
      end
      if available("indent", request.indent) then vim.cmd("runtime! indent/" .. request.indent .. ".vim") end
    end)
  end
  for name, value in pairs(request.options) do vim.bo[cell.buf][name] = value end
  local lines, old = #request.lines > 0 and request.lines or { "" }, cell.lines or { "" }
  local first, old_last, new_last = 1, #old, #lines
  while first <= old_last and first <= new_last and old[first] == lines[first] do first = first + 1 end
  while old_last >= first and new_last >= first and old[old_last] == lines[new_last] do old_last, new_last = old_last - 1, new_last - 1 end
  if first <= old_last or first <= new_last then
    vim.api.nvim_buf_set_lines(cell.buf, first - 1, old_last, false, vim.list_slice(lines, first, new_last))
  end
  cell.lines = lines
  return cell
end
local function retire(id)
  local cell = cells[id]
  if not cell then return end
  for child in pairs(cell.children or {}) do retire(child) end
  vim.api.nvim_buf_delete(cell.buf, { force = true })
  cells[id] = nil
end
function M.run(request)
  if request.action == "retire" then
    retire(request.id)
    return true
  end
  local cell = prepare(request)
  local history_spans = {}
  if request.action == "highlight" then
    local keep = {}
    for _, region in ipairs(request.regions or {}) do
      keep[region.id] = true
      local result = M.run({ action = "highlight", id = region.id, lines = region.lines,
        syntax = request.syntax, indent = request.indent, options = request.options })
      for _, span in ipairs(result.spans) do
        span[1] = span[1] + region.start - request.start
        table.insert(history_spans, span)
      end
    end
    for id in pairs(cell.children or {}) do if not keep[id] then retire(id) end end
    cell.children = keep
  end
  return vim.api.nvim_buf_call(cell.buf, function()
    if request.action == "indent" then
      vim.api.nvim_win_set_cursor(0, { request.lnum, 0 })
      if vim.bo.indentexpr == "" and not vim.bo.cindent and not vim.bo.lisp then
        return request.lnum == 1 and 0 or vim.fn.indent(vim.fn.prevnonblank(request.lnum - 1))
      end
      vim.v.lnum = request.lnum
      local result
      if vim.bo.indentexpr ~= "" then result = vim.fn.eval(vim.bo.indentexpr)
      elseif vim.bo.cindent then result = vim.fn.cindent(request.lnum)
      else result = vim.fn.lispindent(request.lnum) end
      return result >= 0 and result or (request.lnum == 1 and 0 or vim.fn.indent(vim.fn.prevnonblank(request.lnum - 1)))
    end
    local spans = history_spans
    for row, line in ipairs(request.lines) do
      local start, previous = 0, ""
      for column = 1, #line + 1 do
        local group = column <= #line and vim.fn.synIDattr(vim.fn.synIDtrans(vim.fn.synID(row, column, 1)), "name") or ""
        if group ~= previous then
          if previous ~= "" then table.insert(spans, { row - 1, start, column - 1, previous }) end
          start, previous = column - 1, group
        end
      end
    end
    return { spans = spans, indentkeys = vim.bo.indentkeys, syntax_found = request.syntax == "" or available("syntax", request.syntax),
      indent_found = request.indent == "" or available("indent", request.indent) }
  end)
end
return M
