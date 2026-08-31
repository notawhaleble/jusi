local M = {}

M.lines = {
  open = "╭──",
  history = "╞══",
  history_separator = "├┄┄",
  close = "╰──",
}

local kinds = {
  [M.lines.open] = "open",
  [M.lines.history] = "history",
  [M.lines.history_separator] = "history_separator",
  [M.lines.close] = "close",
}

function M.line_kind(line)
  return kinds[line]
end

---Classify a complete buffer conservatively before attaching the 1.0 model.
---An exact legacy delimiter is significant only when no native structural
---line is present; `##` remains valid ordinary text inside a native cell.
---@param lines string[]
---@return "native"|"legacy_0_x"|"unstructured"
function M.detect_format(lines)
  local has_native_structure = false
  local has_legacy_opener = false
  for _, line in ipairs(lines) do
    if M.line_kind(line) then
      has_native_structure = true
    elseif line == "##" then
      has_legacy_opener = true
    end
  end
  if has_native_structure then
    return "native"
  end
  if has_legacy_opener then
    return "legacy_0_x"
  end
  return "unstructured"
end

local function diagnostic(code, row, cell_index, message)
  return {
    code = code,
    row = row,
    cell_index = cell_index,
    message = message,
  }
end

local function finish_cell(cell, end_row, close_row)
  cell.end_row = end_row
  cell.close_row = close_row
  cell.body_end_row = cell.history_row or close_row or end_row
  cell.valid = close_row ~= nil and #cell.diagnostics == 0

  if cell.history_row then
    local entry_start = cell.history_row + 1
    for _, separator_row in ipairs(cell.history_separator_rows) do
      table.insert(cell.history_entries, { start_row = entry_start, end_row = separator_row })
      entry_start = separator_row + 1
    end
    table.insert(cell.history_entries, { start_row = entry_start, end_row = close_row or end_row })
  end
end

---Parse a complete notebook or a recovery-bounded slice.
---@param lines string[]
---@param row_offset? integer zero-based row corresponding to lines[1]
---@return table result
function M.parse(lines, row_offset)
  local offset = row_offset or 0
  local result = { cells = {}, diagnostics = {} }
  local current = nil

  local function add_cell_diagnostic(code, row, message)
    local cell_index = #result.cells + 1
    local item = diagnostic(code, row, cell_index, message)
    table.insert(current.diagnostics, item)
    table.insert(result.diagnostics, item)
  end

  local function begin_cell(row)
    current = {
      open_row = row,
      body_start_row = row + 1,
      body_end_row = nil,
      history_row = nil,
      history_separator_rows = {},
      history_entries = {},
      close_row = nil,
      end_row = nil,
      valid = false,
      diagnostics = {},
    }
  end

  for index, line in ipairs(lines) do
    local row = offset + index - 1
    local kind = M.line_kind(line)

    if kind == "open" then
      if current then
        add_cell_diagnostic("unclosed_cell", current.open_row, "cell reaches another opener before closing")
        finish_cell(current, row, nil)
        table.insert(result.cells, current)
      end
      begin_cell(row)
    elseif kind == "close" then
      if current then
        finish_cell(current, row + 1, row)
        table.insert(result.cells, current)
        current = nil
      else
        table.insert(result.diagnostics, diagnostic("orphan_close", row, nil, "close delimiter has no opener"))
      end
    elseif kind == "history" then
      if not current then
        table.insert(
          result.diagnostics,
          diagnostic("history_outside_cell", row, nil, "history delimiter is outside a cell")
        )
      elseif current.history_row then
        add_cell_diagnostic("duplicate_history_boundary", row, "cell has more than one history boundary")
      else
        current.history_row = row
      end
    elseif kind == "history_separator" then
      if not current then
        table.insert(
          result.diagnostics,
          diagnostic("history_separator_outside_cell", row, nil, "history separator is outside a cell")
        )
      elseif not current.history_row then
        add_cell_diagnostic(
          "history_separator_before_history",
          row,
          "history separator appears before the history boundary"
        )
      else
        table.insert(current.history_separator_rows, row)
      end
    end
  end

  if current then
    local end_row = offset + #lines
    add_cell_diagnostic("unclosed_cell", current.open_row, "cell reaches the end of the parsed region before closing")
    finish_cell(current, end_row, nil)
    table.insert(result.cells, current)
  end

  return result
end

return M
