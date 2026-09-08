local M = {}
function M.run()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
    '╭──', '%%sql main', 'select 1', '╞══', 'select 2', '├┄┄', 'select 3', '╰──',
    '╭──', 'print(42)', '╰──',
  })
  local editor = require('jusi.editing').attach(buf)
  local model, history = editor.model, editor.history
  local function settle()
    model:flush()
    local drained = false
    vim.schedule(function() vim.schedule(function() drained = true end) end)
    assert(vim.wait(1000, function() return drained and not history.scheduled and not editor.scheduled end))
  end
  local cell = model:cell_at_row(0)
  history:refresh()
  assert(vim.fn.foldclosed(4) == 4 and vim.fn.foldclosedend(4) == 7)
  assert(vim.fn.foldclosed(8) == -1, 'closer must remain visible')
  assert(vim.fn.foldtextresult(4) == '╞══ history: 2 entries')
  vim.api.nvim_win_set_cursor(0, { 2, 0 }); assert(history:toggle())
  assert(vim.fn.foldclosed(4) == -1)
  local first_win = vim.api.nvim_get_current_win()
  vim.cmd('vsplit'); local second_win = vim.api.nvim_get_current_win()
  history:refresh()
  assert(vim.fn.foldclosed(4) == 4 and vim.fn.foldlevel(4) == 1, 'new split inherited duplicate folds')
  vim.api.nvim_set_current_win(first_win)
  assert(vim.fn.foldclosed(4) == -1, 'windows must have independent fold state')
  vim.api.nvim_buf_set_lines(buf, 2, 3, false, { 'select 10', 'from t' })
  settle(); history:refresh()
  assert(vim.fn.foldclosed(5) == -1, 'typing reset opened fold')
  history:capture(cell.id, '%%sql main\nselect 2')
  history:refresh()
  assert(vim.deep_equal(model:history(cell.id), { { 'select 2' }, { 'select 3' } }))
  assert(vim.fn.foldclosed(5) == -1, 'capture reset opened fold')
  history:capture(cell.id, '%%sql main\nselect 4')
  history:refresh()
  assert(#model:history(cell.id) == 3)
  vim.api.nvim_win_set_cursor(0, { 6, 0 })
  -- Separate programmatic edits as interactive command boundaries do.
  vim.cmd('let &ul = &ul')
  assert(history:apply())
  assert(vim.deep_equal(model:body(cell.id), { '%%sql main', 'select 4' }))
  assert(vim.fn.foldclosed(4) == 4)
  vim.cmd('undo'); settle(); history:refresh()
  assert(vim.deep_equal(model:body(cell.id), { '%%sql main', 'select 10', 'from t' }))
  assert(#model:history(cell.id) == 3, 'apply undo changed history')
  -- Damaging the boundary reveals uncertain text; repair retains identity.
  local s = model:cell_snapshot(cell.id)
  vim.api.nvim_buf_set_lines(buf, s.history_row, s.history_row + 1, false, { 'broken' })
  settle(); history:refresh()
  assert(vim.fn.foldclosed(s.history_row + 1) == -1)
  vim.api.nvim_buf_set_lines(buf, s.history_row, s.history_row + 1, false, { '╞══' })
  settle(); history:refresh()
  assert(model:cell_at_row(0).id == cell.id)
  -- A temporarily invalid closer defers accepted history until repair.
  s = model:cell_snapshot(cell.id)
  vim.api.nvim_buf_set_lines(buf, s.close_row, s.close_row + 1, false, { 'broken closer' })
  settle()
  local delayed = { kind = 'followup', trace_id = 'damaged', body = 'select delayed' }
  history:submit(delayed, cell.id); history:result(delayed, {})
  assert(history.deferred[cell.id])
  vim.api.nvim_buf_set_lines(buf, s.close_row, s.close_row + 1, false, { '╰──' })
  settle()
  assert(model:history(cell.id)[1][1] == 'select delayed' and not history.deferred[cell.id])
  -- Capture exact submitted text, once, despite edits and HTTP/SSE ordering.
  local command = { kind = 'execute', trace_id = 'initial', code = '%%sql main\nselect 88' }
  history:submit(command, cell.id)
  history:result(command, { execution = {} })
  history:event({ kind = 'client.created', trace_id = 'initial', payload = { capabilities = { 'followup' } } })
  assert(model:history(cell.id)[1][1] == 'select 88')
  local follow = { kind = 'followup', trace_id = 'follow', body = 'select 99' }
  history:submit(follow, cell.id)
  history:event({ kind = 'operation.completed', trace_id = 'follow', payload = { outcome = 'succeeded' } })
  history:result(follow, {})
  assert(model:history(cell.id)[1][1] == 'select 99')
  local no_history = { kind = 'execute', trace_id = 'plain', code = 'print(1)' }
  history:submit(no_history, cell.id)
  assert(not history.pending.plain)
  no_history.trace_id, no_history.code = 'one_shot', '%%once\nx'
  history:submit(no_history, cell.id)
  local before = model:history(cell.id)
  history:event({ kind = 'client.created', trace_id = 'one_shot', payload = { capabilities = {} } })
  assert(vim.deep_equal(before, model:history(cell.id)))
  local count = #model:history(cell.id)
  follow.trace_id = 'rejected'; history:submit(follow, cell.id)
  history:result(follow, nil, { layer = 'supervisor' })
  assert(#model:history(cell.id) == count)
  follow.trace_id, follow.body = 'failed', 'select bad'
  history:submit(follow, cell.id); history:result(follow, nil, { layer = 'plugin_worker' })
  assert(model:history(cell.id)[1][1] == 'select bad')
  follow.trace_id, follow.body = 'empty', ''
  history:submit(follow, cell.id); history:result(follow, {})
  assert(vim.deep_equal(model:history(cell.id)[1], { '' }))
  -- Retired identities cannot receive late history.
  command.trace_id = 'retired'; history:submit(command, cell.id)
  s = model:cell_snapshot(cell.id)
  vim.api.nvim_buf_set_lines(buf, s.open_row, s.end_row, false, {})
  settle(); history:refresh()
  history:event({ kind = 'client.created', trace_id = 'retired', payload = { capabilities = { 'followup' } } })
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(buf, 0, -1, false), { '╭──', 'print(42)', '╰──' }))
  assert(vim.fn.foldclosed(1) == -1)
  vim.api.nvim_win_close(second_win, true)
  editor:close(); vim.api.nvim_buf_delete(buf, { force = true })

  -- Each expanded history entry has independent native syntax and indentation.
  buf = vim.api.nvim_create_buf(false, true); vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { '╭──', 'x = 1', '╞══', '"""open', '├┄┄', 'if True:', '    print(42)', '', '╰──' })
  editor = require('jusi.editing').attach(buf); history = editor.history; model = editor.model
  history:refresh(); history:toggle(); editor:refresh()
  cell = model:cell_at_row(0)
  local found = false
  for _, mark in ipairs(vim.api.nvim_buf_get_extmarks(buf, editor.namespaces[cell.id], 0, -1, { details = true })) do
    if mark[2] == 5 and mark[4].hl_group == 'Statement' then found = true end
  end
  assert(found, 'history string leaked into next entry')
  assert(editor:indent(6) == 0 and editor:indent(8) == 4)
  assert(vim.fn.foldclosed(3) == -1)
  local other = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(other)
  vim.api.nvim_set_current_buf(buf)
  history:refresh()
  assert(vim.fn.foldclosed(3) == -1, 'returning to buffer reset fold state')
  editor:close()
  editor = require('jusi.editing').attach(buf)
  editor.history:refresh()
  assert(vim.fn.foldclosed(3) == -1, 'model replacement reset fold state')
  vim.api.nvim_buf_delete(other, { force = true })
  editor:close(); vim.api.nvim_buf_delete(buf, { force = true })
end
return M
