local notebook = require("jusi.notebook")
local marks_module = require("jusi.marks")
local M = {}
function M.run()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "a", "╰──", "╭──", "b", "╰──" })
  local model = notebook.attach(buf)
  local controller = { clients = {}, executions = {} }
  local marks = marks_module.new(model, controller)
  model.on_cells_retired = function(ids) for _, id in ipairs(ids) do marks:retire(id) end end
  local a, b = model:cell_at_row(1).id, model:cell_at_row(4).id
  local original = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local tick, signcolumn = vim.api.nvim_buf_get_changedtick(buf), vim.wo.signcolumn
  assert(next(marks.records) == nil, "never executed cells should be blank")
  local function inspect_closer(id, group)
    local snapshot = model:cell_snapshot(id)
    local mark = vim.api.nvim_buf_get_extmark_by_id(buf, marks.namespace, assert(marks.closers[id]), { details = true })
    assert(mark[1] == snapshot.close_row and mark[2] == 0)
    assert(mark[3].hl_group == group and mark[3].end_col == #"╰──")
  end
  local function inspect_idle(id)
    local mark = vim.api.nvim_buf_get_extmark_by_id(buf, marks.namespace, assert(marks.idle[id]), { details = true })
    assert(mark[3].hl_group == "JusiCellIdle" and mark[3].virt_text == nil and mark[3].sign_text == nil)
    inspect_closer(id, "JusiCellIdle")
  end
  inspect_idle(a)
  inspect_idle(b)
  for group, color in pairs({ Busy = 0xc678dd, Followup = 0x61afef, Idle = 0xe5c07b, Interrupted = 0xd19a66 }) do
    assert(vim.api.nvim_get_hl(0, { name = "JusiCell" .. group }).fg == color)
  end
  for group, color in pairs({ Busy = 176, Done = 114, Error = 168, Interrupted = 173,
    Followup = 75, Idle = 180, Unknown = 102 }) do
    assert(vim.api.nvim_get_hl(0, { name = "JusiCell" .. group }).ctermfg == color)
  end
  local function execution(id, cell, result)
    return { execution_id = id, cell_id = cell, notebook_id = model.notebook_id, outcome = result }
  end
  local function inspect(cell, state, glyph, group)
    local record = assert(marks.records[cell])
    assert(record.state == state, vim.inspect(record))
    local mark = vim.api.nvim_buf_get_extmark_by_id(buf, marks.namespace, record.mark, { details = true })
    assert(mark[3].virt_text_pos == "eol" and mark[3].sign_text == nil)
    assert(vim.deep_equal(mark[3].virt_text, { { glyph, group } }))
    assert(mark[3].hl_group == group)
    return mark
  end
  marks:execution(execution("exe_a", a, "running"))
  inspect(a, "busy", "*", "JusiCellBusy")
  assert(marks.idle[a] == nil)
  inspect_closer(a, "JusiCellBusy")
  marks:event({ kind = "execution.input_requested", operation = "execute", resource = { kind = "execution" }, payload = {} })
  inspect(a, "busy", "*", "JusiCellBusy")
  marks:execution(execution("exe_a", a, "succeeded"))
  inspect(a, "done", "✓", "JusiCellDone")
  inspect_closer(a, "JusiCellDone")
  marks:execution(execution("exe_b", b, "running"))
  marks:execution(execution("exe_b", b, "failed"))
  inspect(b, "error", "✗", "JusiCellError")
  marks:execution(execution("exe_new", a, "running"))
  marks:execution(execution("exe_a", a, "failed"))
  inspect(a, "busy", "*", "JusiCellBusy")
  marks:execution(execution("exe_new", a, "interrupted"))
  inspect(a, "interrupted", "!", "JusiCellInterrupted")
  assert(vim.deep_equal(original, vim.api.nvim_buf_get_lines(buf, 0, -1, false)))
  assert(tick == vim.api.nvim_buf_get_changedtick(buf) and signcolumn == vim.wo.signcolumn)

  local client = { client_id = "client_b", execution_id = "exe_b", cell_id = b,
    notebook_id = model.notebook_id, capabilities = { "followup" } }
  controller.clients.client_b = client
  marks:client(client)
  inspect(b, "followup", ">", "JusiCellFollowup")
  local function operation(kind, id, name, result)
    marks:event({ kind = kind, operation = name or "followup", resource = { kind = "client", id = "client_b" },
      payload = { operation_id = id, outcome = result } })
  end
  operation("operation.started", "op_completion", "complete")
  inspect(b, "followup", ">", "JusiCellFollowup")
  operation("operation.started", "op_1")
  inspect(b, "busy", "*", "JusiCellBusy")
  operation("operation.completed", "op_old", "followup", "succeeded")
  inspect(b, "busy", "*", "JusiCellBusy")
  operation("operation.completed", "op_1", "followup", "succeeded")
  inspect(b, "followup", ">", "JusiCellFollowup")

  -- Pure editing moves projections without any redraw or status/backend work.
  local mark_id = marks.records[b].mark
  vim.api.nvim_buf_set_lines(buf, 1, 1, false, { "extra", "lines" })
  local moved = inspect(b, "followup", ">", "JusiCellFollowup")
  assert(moved[1] == 5 and marks.records[b].mark == mark_id)
  vim.api.nvim_buf_set_lines(buf, 4, 5, false, { "damaged closer" })
  model:flush()
  inspect(a, "interrupted", "!", "JusiCellInterrupted")
  assert(vim.wait(1000, function() return marks.closers[a] == nil end))
  -- Restoring a closer takes the existing cell status, not the idle color.
  vim.api.nvim_buf_set_lines(buf, 4, 5, false, { "╰──" })
  model:flush()
  assert(vim.wait(1000, function() return marks.closers[a] ~= nil end))
  inspect_closer(a, "JusiCellInterrupted")
  -- Merge by deleting A's closer and B's opener. B's status is not inherited
  -- by the merged cell or restored by undo.
  vim.cmd("let &l:undolevels = &l:undolevels")
  vim.api.nvim_buf_set_lines(buf, 4, 6, false, {})
  model:flush()
  assert(vim.wait(1000, function() return marks.records[b] == nil end))
  inspect(a, "interrupted", "!", "JusiCellInterrupted")
  inspect_closer(a, "JusiCellInterrupted")
  assert(marks.closers[b] == nil)
  vim.cmd("undo")
  model:flush()
  local recreated = model:cell_at_row(6).id
  assert(recreated ~= b and marks.records[recreated] == nil)
  assert(vim.wait(1000, function() return marks.idle[recreated] ~= nil end))
  inspect_idle(recreated)
  marks:execution(execution("exe_b", b, "succeeded"))
  assert(marks.records[b] == nil)

  controller.clients = {}
  controller.executions = { current = execution("exe_current", a, "running") }
  marks:resync({ reason = "cursor_expired" })
  inspect(a, "busy", "*", "JusiCellBusy")
  controller.executions = {}
  marks:resync({ reason = "cursor_expired" })
  inspect(a, "unknown", "!", "JusiCellUnknown")
  marks:resync({ reason = "supervisor_replaced" })
  assert(next(marks.records) == nil)
  marks:close()
  model:detach()
  vim.api.nvim_buf_delete(buf, { force = true })
end
return M
