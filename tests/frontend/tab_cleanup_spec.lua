local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local initial = vim.api.nvim_get_current_tabpage()
  vim.cmd.tabnew()
  local original_tab, buf = vim.api.nvim_get_current_tabpage(), vim.api.nvim_get_current_buf()
  vim.bo[buf].bufhidden = 'hide'
  local lines = { '╭──', 'a', '╰──', '╭──', 'b', '╰──', '╭──', 'c', '╰──',
    '╭──', 'd', '╰──', '╭──', 'e', '╰──' }
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  local model = require('jusi.notebook').attach(buf)
  local cells = model:ordered_cells()
  local presentation = require('jusi.presentation').new({ notebook_id = model.notebook_id, notebook_buf = buf })
  local original_output = presentation:write(cells[3].id, { execution_id = 'exe_original', media_type = 'text/plain', data = 'keep' })
  local interactive = { surfaces = {} }
  local calls, replies, failures = {}, {}, {}
  local controller = { kernel_state = 'on', clients = {}, executions = {},
    interrupt = function(_, id, callback) calls[#calls+1] = { 'interrupt', id }; replies[id] = callback end,
    close_client = function(_, id, callback) calls[#calls+1] = { 'close', id }; replies[id] = callback end }
  local buffers = {}
  local function client(id, cell, visible)
    local output = vim.api.nvim_create_buf(false, true)
    buffers[#buffers+1] = output
    vim.bo[output].bufhidden = 'hide'
    vim.b[output].jusi_notebook_id, vim.b[output].jusi_cell_id = model.notebook_id, cell.id
    vim.b[output].jusi_role = 'interactive_terminal'
    require('jusi.output_ids').attach(output)
    local record = { buf = output, client = { client_id = id, cell_id = cell.id } }
    interactive.surfaces[id] = record
    controller.clients[id] = record.client
    if visible then vim.api.nvim_open_win(output, false, { split = 'below' }) end
    return output
  end
  local shared = client('cli_shared', cells[2], true)
  local hidden = client('cli_hidden', cells[4], false)
  local lifecycle = require('jusi.cell_lifecycle').new({ model = model, presentation = presentation,
    controller = controller, on_failure = function(failure) failures[#failures+1] = failure end })
  local session = { buf = buf, model = model, presentation = presentation, interactive = interactive,
    controller = controller, lifecycle = lifecycle }
  jusi._sessions[buf] = session
  local detach_mappings = require('jusi.mappings').attach(buf)
  local mode = require('jusi.cellmode').new({ model = model })
  local function resolve(id, failure)
    local callback = assert(replies[id]); replies[id] = nil
    if not failure and controller.clients[id] then
      local record = interactive.surfaces[id]
      require('jusi.presentation.window').close_for_buffer(record.buf)
      vim.api.nvim_buf_delete(record.buf, { force = true })
      interactive.surfaces[id], controller.clients[id] = nil, nil
    end
    callback(failure and nil or {}, failure)
  end
  vim.cmd.tabnew()
  local project_tab, project_buf = vim.api.nvim_get_current_tabpage(), vim.api.nvim_get_current_buf()
  local project_win = vim.api.nvim_get_current_win()
  local mirror = vim.api.nvim_open_win(buf, true, { split = 'left' })
  local local_buf = client('cli_local', cells[1], true)
  local shared_win = vim.api.nvim_open_win(shared, false, { split = 'below' })
  local local_output = presentation:write(cells[5].id, { execution_id = 'exe_local_text', media_type = 'text/plain', data = 'remove' })
  controller.executions.exe_local = { cell_id = cells[1].id, outcome = 'running' }
  local function keys(value)
    vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(value, true, false, true), 'xt', false)
  end
  keys('\\Q')
  assert(#calls == 1 and calls[1][1] == 'interrupt' and calls[1][2] == 'exe_local')
  assert(vim.api.nvim_win_is_valid(mirror), 'notebook closed before backend cleanup completed')
  assert(not vim.api.nvim_buf_is_valid(local_output.buf))
  assert(not vim.api.nvim_win_is_valid(shared_win) and vim.api.nvim_buf_is_valid(shared))
  assert(vim.api.nvim_buf_is_valid(original_output.buf) and controller.clients.cli_hidden)
  -- Cell mode retains the same literal-backslash command; duplicate use is coalesced.
  mode:set(true); keys('\\Q')
  assert(#calls == 1)
  assert(vim.api.nvim_buf_call(local_buf, function() return vim.fn.maparg('\\Q', 'n') ~= '' end))
  resolve('exe_local')
  assert(#calls == 2 and calls[2][2] == 'cli_local')
  vim.api.nvim_set_current_tabpage(original_tab)
  resolve('cli_local')
  assert(not vim.api.nvim_win_is_valid(mirror) and vim.api.nvim_get_current_tabpage() == original_tab)
  assert(vim.api.nvim_win_is_valid(project_win) and vim.api.nvim_win_get_buf(project_win) == project_buf)
  assert(vim.api.nvim_buf_is_valid(shared) and controller.clients.cli_shared and controller.clients.cli_hidden)
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(buf, 0, -1, false), lines) and controller.kernel_state == 'on')
  -- The last notebook view survives, while clients visible only here end.
  vim.api.nvim_set_current_win(assert(require('jusi.presentation.window').find(buf, original_tab)))
  vim.cmd.JusiCloseTab()
  assert(replies.cli_shared and not vim.api.nvim_buf_is_valid(original_output.buf))
  resolve('cli_shared')
  assert(#vim.fn.win_findbuf(buf) == 1 and controller.clients.cli_hidden and vim.api.nvim_buf_is_valid(hidden))
  -- Failure leaves the notebook view for retry; repurposed windows are never closed.
  vim.api.nvim_set_current_tabpage(project_tab)
  mirror = vim.api.nvim_open_win(buf, true, { split = 'left' })
  client('cli_retry', cells[1], true)
  controller.executions.exe_local.outcome = 'interrupted'
  jusi.close_tab()
  resolve('cli_retry', { reason = 'unreachable' })
  assert(#failures == 1 and vim.api.nvim_win_is_valid(mirror) and controller.clients.cli_retry)
  jusi.close_tab()
  vim.api.nvim_win_set_buf(mirror, project_buf)
  resolve('cli_retry')
  assert(vim.api.nvim_win_is_valid(mirror) and vim.api.nvim_win_get_buf(mirror) == project_buf)
  -- A runtime replacement invalidates the captured notebook-close continuation.
  vim.api.nvim_win_set_buf(mirror, buf)
  client('cli_old', cells[1], true)
  jusi.close_tab()
  jusi._sessions[buf] = {}
  resolve('cli_old')
  assert(vim.api.nvim_win_is_valid(mirror))
  jusi._sessions[buf] = session
  lifecycle:detach()
  jusi._sessions[buf] = nil
  -- Offline notebook cleanup needs no session and still keeps the last view.
  vim.api.nvim_set_current_win(mirror)
  vim.cmd.JusiCloseTab()
  assert(not vim.api.nvim_win_is_valid(mirror) and #vim.fn.win_findbuf(buf) == 1)
  vim.api.nvim_set_current_tabpage(original_tab)
  vim.api.nvim_set_current_win(assert(require('jusi.presentation.window').find(buf, original_tab)))
  vim.api.nvim_open_win(buf, true, { split = 'below' })
  vim.cmd.JusiCloseTab()
  assert(#vim.fn.win_findbuf(buf) == 1, 'offline cleanup removed the last notebook view')
  mode:close(); detach_mappings(); presentation:close()
  model:detach()
  vim.api.nvim_set_current_tabpage(project_tab); vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(original_tab); vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(initial)
  for _, output in ipairs(buffers) do
    if vim.api.nvim_buf_is_valid(output) then vim.api.nvim_buf_delete(output, { force = true }) end
  end
  vim.api.nvim_buf_delete(buf, { force = true })
  vim.api.nvim_buf_delete(project_buf, { force = true })
end
return M
