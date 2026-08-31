local notebook = require("jusi.notebook")
local parser = require("jusi.notebook.parser")

local M = {}

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
  end
end

local function truthy(value, message)
  if not value then
    error(message or "expected a truthy value")
  end
end

local function buffer_with(lines)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  return buf
end

local function cells(model)
  return model:ordered_cells()
end

local function diagnostic_codes(result)
  local codes = {}
  for _, item in ipairs(result.diagnostics) do
    table.insert(codes, item.code)
  end
  return codes
end

local function test_parser_valid_cell_and_history()
  local result = parser.parse({
    "╭──",
    "%%sql main",
    "select * from current_orders;",
    "╞══",
    "select count(*) from orders;",
    "├┄┄",
    "select * from orders limit 20;",
    "├┄┄",
    "select * from orders;",
    "╰──",
  })
  equal(#result.cells, 1)
  equal(result.diagnostics, {})
  local cell = result.cells[1]
  truthy(cell.valid)
  equal({ cell.body_start_row, cell.body_end_row, cell.history_row, cell.close_row }, { 1, 3, 3, 9 })
  equal(cell.history_entries, {
    { start_row = 4, end_row = 5 },
    { start_row = 6, end_row = 7 },
    { start_row = 8, end_row = 9 },
  })
end

local function test_parser_exact_lines_and_local_recovery()
  local result = parser.parse({
    " ╭──",
    "╰──",
    "╭──",
    "first",
    "├┄┄",
    "╭──",
    "second",
    "╰──",
    "╞══",
  })
  equal(#result.cells, 2)
  equal(diagnostic_codes(result), {
    "orphan_close",
    "history_separator_before_history",
    "unclosed_cell",
    "history_outside_cell",
  })
  equal(result.cells[1].open_row, 2)
  equal(result.cells[1].end_row, 5)
  equal(result.cells[2].open_row, 5)
  truthy(result.cells[2].valid)
end

local function test_model_body_edit_is_local_and_preserves_identity()
  local buf = buffer_with({ "╭──", "one", "╰──", "╭──", "two", "╰──" })
  local model = notebook.attach(buf)
  local first_id = cells(model)[1].id
  local second_id = cells(model)[2].id

  vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "one changed", "one more" })

  equal(model.last_change.kind, "body")
  equal(model.last_change.affected_cell_ids, { first_id })
  equal(model.metrics.full_parse_count, 1)
  equal(model.metrics.structural_reconcile_count, 0)
  equal(model:body(first_id), { "one changed", "one more" })
  equal(cells(model)[1].id, first_id)
  equal(cells(model)[2].id, second_id)
  equal(model:cell_snapshot(second_id).open_row, 4)
  model:detach()
end

local function test_structural_split_merge_and_deleted_identity_retirement()
  local buf = buffer_with({ "╭──", "one", "two", "╰──" })
  local model = notebook.attach(buf)
  local original_id = cells(model)[1].id

  vim.api.nvim_buf_set_lines(buf, 2, 2, false, { "╰──", "╭──" })
  equal(#cells(model), 2)
  equal(cells(model)[1].id, original_id)
  local split_id = cells(model)[2].id
  truthy(split_id ~= original_id)
  equal(model.metrics.max_structural_scan, 6)

  vim.api.nvim_buf_set_lines(buf, 2, 4, false, {})
  equal(#cells(model), 1)
  equal(cells(model)[1].id, original_id)
  equal(model:body(original_id), { "one", "two" })
  equal(model:cell_by_id(split_id), nil)

  vim.api.nvim_buf_set_lines(buf, 2, 2, false, { "╰──", "╭──" })
  equal(#cells(model), 2)
  truthy(cells(model)[2].id ~= split_id, "a deleted cell identity must not resurrect")
  model:detach()
end

local function test_deleted_opener_never_reuses_identity()
  local buf = buffer_with({ "╭──", "body", "╰──" })
  local model = notebook.attach(buf)
  local deleted_id = cells(model)[1].id

  vim.api.nvim_buf_set_lines(buf, 0, 1, false, {})
  equal(#cells(model), 0)
  equal(diagnostic_codes({ diagnostics = model:diagnostics() }), { "orphan_close" })

  vim.api.nvim_buf_set_lines(buf, 0, 0, false, { "╭──" })
  equal(#cells(model), 1)
  truthy(cells(model)[1].id ~= deleted_id, "restored text must create a fresh model identity")
  model:detach()
end

local function test_broken_cell_recovers_at_next_opener()
  local buf = buffer_with({ "╭──", "one", "╰──", "╭──", "two", "╰──" })
  local model = notebook.attach(buf)
  local first_id = cells(model)[1].id
  local second_id = cells(model)[2].id

  vim.api.nvim_buf_set_lines(buf, 2, 3, false, {})

  equal(#cells(model), 2)
  equal(cells(model)[1].id, first_id)
  equal(cells(model)[2].id, second_id)
  equal(model:cell_snapshot(first_id).valid, false)
  equal(model:cell_snapshot(second_id).valid, true)
  equal(model.last_change.scanned_line_count, 2)
  equal(diagnostic_codes({ diagnostics = model:diagnostics() }), { "unclosed_cell" })
  model:detach()
end

local function test_partial_delimiter_edit_reparses_structure()
  local buf = buffer_with({ "╭──", "body", "╰──", "╭──", "next", "╰──" })
  local model = notebook.attach(buf)
  local first_id = cells(model)[1].id
  local second_id = cells(model)[2].id

  vim.api.nvim_buf_set_text(buf, 2, 0, 2, 1, { "x" })

  equal(model.last_change.kind, "structural")
  equal(model:cell_snapshot(first_id).valid, false)
  equal(model:cell_snapshot(second_id).valid, true)
  equal(diagnostic_codes({ diagnostics = model:diagnostics() }), { "unclosed_cell" })
  model:detach()
end

local function test_model_extracts_active_body_and_history()
  local buf = buffer_with({ "╭──", "current", "╞══", "newest", "├┄┄", "oldest", "╰──" })
  local model = notebook.attach(buf)
  local cell = cells(model)[1]
  equal(model:body(cell), { "current" })
  equal(model:history(cell), { { "newest" }, { "oldest" } })
  model:detach()
end

function M.run()
  test_parser_valid_cell_and_history()
  test_parser_exact_lines_and_local_recovery()
  test_model_body_edit_is_local_and_preserves_identity()
  test_structural_split_merge_and_deleted_identity_retirement()
  test_deleted_opener_never_reuses_identity()
  test_broken_cell_recovers_at_next_opener()
  test_partial_delimiter_edit_reparses_structure()
  test_model_extracts_active_body_and_history()
end

return M
