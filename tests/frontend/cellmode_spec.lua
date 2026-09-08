local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
    'outside', '╭──', '%%sql main', 'select 1', '╞══', 'select 2', '├┄┄', 'select 3', '╰──',
    '', '╭──', '42', '╰──', '╭──', '╰──',
  })
  local original_j = function() end
  vim.keymap.set('n', 'j', original_j, { buffer = buf })
  local editor = require('jusi.editing').attach(buf)
  local mode, model = editor.cellmode, editor.model
  editor.history:refresh()
  local cells = model:ordered_cells()
  local original_text = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  local function row() return vim.api.nvim_win_get_cursor(0)[1] end
  local function keys(value)
    vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(value, true, false, true), 'xt', false)
  end
  keys('<Space>')
  assert(mode.enabled and vim.b.jusi_cell_mode_active)
  assert(vim.api.nvim_get_hl(0, { name = 'JusiCellIdleMode' }).reverse)
  local marks = vim.api.nvim_buf_get_extmarks(buf, editor.offline_marks.namespace, 0, -1, { details = true })
  assert(marks[1][4].hl_group == 'JusiCellIdleMode')
  assert(vim.deep_equal(original_text, vim.api.nvim_buf_get_lines(buf, 0, -1, false)))
  keys('j'); assert(row() == 3)
  keys('j'); assert(row() == 12, 'folded history was visited')
  keys('k'); assert(row() == 3)
  keys('Hj'); assert(row() == 6)
  keys('j'); assert(row() == 8)
  keys('j'); assert(row() == 12)
  keys('k'); assert(row() == 8, 'backwards motion skipped previous expanded history')
  keys('k'); assert(row() == 6)
  keys('k'); assert(row() == 3)
  keys('3j'); assert(row() == 12)
  vim.cmd('JusiNextCell'); assert(row() == 14, 'empty cell must land on opener')
  vim.cmd('JusiNextCell'); assert(row() == 14, 'last-cell motion wrapped')
  vim.cmd('2JusiPreviousCell'); assert(row() == 3)
  keys('j<CR>')
  assert(vim.deep_equal(model:body(cells[1]), { '%%sql main', 'select 2' }), 'Enter did not restore history offline')
  assert(vim.fn.foldclosed(5) == 5)
  keys('<C-P>')
  assert(model:body(cells[1])[2] == 'select 3')
  keys('<C-N>')
  assert(model:body(cells[1])[2] == 'select 2')
  local parse_count = model.metrics.full_parse_count
  keys('Y')
  vim.cmd('JusiCellPasteBelow')
  local pasted = model:cell_at_row(row() - 1)
  assert(pasted.id ~= cells[1].id and model:cell_by_id(cells[1].id))
  assert(#model:history(pasted) == 2 and #model:ordered_cells() == 4)
  vim.cmd('JusiCellDelete')
  assert(not model:cell_by_id(pasted.id) and #model:ordered_cells() == 3)
  assert(model.metrics.full_parse_count == parse_count)
  keys('<Space>')
  assert(not mode.enabled and vim.fn.maparg('j', 'n', false, true).callback == original_j)
  keys('<Space>')
  local replacement = function() end
  vim.keymap.set('n', 'j', replacement, { buffer = buf })
  keys('<Space>')
  assert(vim.fn.maparg('j', 'n', false, true).callback == replacement, 'leaving mode destroyed a user remap')
  -- Native Insert behavior is preserved; the mode resumes afterward.
  mode:set(true)
  vim.api.nvim_win_set_cursor(0, { 3, 0 })
  local checked = false
  _G.JusiCellModeInsertTest = function()
    assert(vim.fn.mode():sub(1, 1) == 'i' and not vim.b.jusi_cell_mode_active)
    checked = true
  end
  keys('A<Cmd>lua JusiCellModeInsertTest()<CR><Esc>')
  _G.JusiCellModeInsertTest = nil
  assert(checked and mode.enabled and vim.b.jusi_cell_mode_active)
  -- C clears only the payload, preserving identity, header and history.
  keys('Cnew body<Esc>')
  assert(vim.deep_equal(model:body(cells[1].id), { '%%sql main', 'new body' }))
  assert(#model:history(cells[1].id) == 2)
  -- Creating cells is local and gives each opener a fresh identity.
  vim.cmd('JusiCellNewAbove'); vim.cmd('stopinsert')
  local created = model:cell_at_row(row() - 1)
  assert(created.id ~= cells[1].id and vim.deep_equal(model:body(created), { '' }))
  assert(model:cell_by_id(cells[1].id))
  vim.cmd('JusiCellDelete')
  assert(not model:cell_by_id(created.id))
  editor:close()
  assert(vim.fn.maparg('j', 'n', false, true).callback == replacement)
  vim.api.nvim_buf_delete(buf, { force = true })

  -- Submission precedence is independent from presentation marks.
  buf = vim.api.nvim_create_buf(false, true); vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { '╭──', 'body', '╰──' })
  editor = require('jusi.editing').attach(buf); model = editor.model
  local cell = model:cell_at_row(1)
  local controller = { clients = {}, executions = {} }
  jusi._sessions[buf] = { buf = buf, model = model, controller = controller }
  local saved, calls = {}, {}
  for _, name in ipairs({ 'execute', 'followup', 'input' }) do
    saved[name] = jusi[name]
    jusi[name] = function() table.insert(calls, name) end
  end
  vim.api.nvim_win_set_cursor(0, { 2, 0 })
  jusi.submit(); assert(calls[#calls] == 'execute')
  controller.clients.client = { cell_id = cell.id, notebook_id = model.notebook_id, capabilities = { 'followup' } }
  jusi.submit(); assert(calls[#calls] == 'followup')
  controller.pending_input = { cell_id = cell.id }
  controller.executions.execution = { cell_id = cell.id, outcome = 'running' }
  jusi.submit(); assert(calls[#calls] == 'input')
  local projection = vim.api.nvim_create_buf(false, true)
  vim.b[projection].jusi_notebook_id = model.notebook_id
  vim.b[projection].jusi_cell_id = cell.id
  jusi.submit(projection, 999)
  assert(calls[#calls] == 'input', 'projection submission used terminal screen coordinates')
  vim.api.nvim_buf_delete(projection, { force = true })
  controller.pending_input = nil
  local count = #calls
  jusi.submit(); assert(#calls == count, 'busy cell submitted new work')
  for name, callback in pairs(saved) do jusi[name] = callback end
  jusi._sessions[buf] = nil
  editor:close(); vim.api.nvim_buf_delete(buf, { force = true })
end
return M
