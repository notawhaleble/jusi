-- Explicit cursor motions use the linked cell model; they never reparse a notebook.
local M = {}
function M.body_row(snapshot)
  return snapshot.body_start_row < snapshot.body_end_row and snapshot.body_start_row or snapshot.open_row
end
local function targets(snapshot, history)
  local rows = { M.body_row(snapshot) }
  if history and snapshot.valid and snapshot.history_row and vim.fn.foldclosed(snapshot.history_row + 1) < 0 then
    for _, entry in ipairs(snapshot.history_entries) do
      table.insert(rows, entry.start_row < entry.end_row and entry.start_row or entry.start_row - 1)
    end
  end
  return rows
end
function M.move(model, direction, count, history)
  local row = vim.api.nvim_win_get_cursor(0)[1] - 1
  for _ = 1, count or 1 do
    local cell = model:cell_at_row(row)
    local target
    if cell then
      local rows = targets(model:cell_snapshot(cell), history)
      if history then
        if direction > 0 then
          for _, candidate in ipairs(rows) do if candidate > row then target = candidate; break end end
        else
          for i = #rows, 1, -1 do if rows[i] < row then target = rows[i]; break end end
        end
      end
      if not target then
        if direction > 0 then cell = cell.next else cell = cell.prev end
      end
    else
      if direction > 0 then cell = model:_next_open_at_or_after(row) else cell = model:_nearest_open_before(row) end
    end
    if not target and cell then
      local rows = targets(model:cell_snapshot(cell), history)
      target = direction < 0 and rows[#rows] or rows[1]
    end
    if not target or target == row then break end
    row = target
  end
  vim.api.nvim_win_set_cursor(0, { row + 1, 0 })
  return model:cell_at_row(row)
end
return M
