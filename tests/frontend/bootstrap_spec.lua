local M = {}
function M.run()
  local function read(path) return vim.json.decode(table.concat(vim.fn.readfile('protocol/fixtures/v1/' .. path), '\n')) end
  local protocol = require('jusi.protocol')
  assert(protocol.validate_command(read('valid/bootstrap.json'), 'execute'))
  assert(not protocol.validate_command(read('invalid/bootstrap-nonboolean.json'), 'execute'))
  assert(protocol.validate_plugin_catalog(read('valid/bootstrap-catalog.json')))
  local buf = vim.api.nvim_create_buf(false, true)
  local original = { '╭──', '%%sql main', 'private unfinished draft', '╰──' }
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, original)
  local editor = require('jusi.editing').attach(buf)
  local model, requests = editor.model, {}
  local cell = model:cell_at_row(1)
  local controller = require('jusi.controller').new({ notebook = model, transport = {
    request = function(_, method, path, command, options, callback)
      requests[#requests + 1] = { command = command, callback = callback }
      return requests[#requests]
    end,
  } })
  controller.kernel_state, controller.kernel_id = 'on', 'kernel_test'
  controller.plugin_catalog = { plugins = { { families = { { magic_name = 'sql' } } } } }
  controller.on_submission = function(command, id) editor.history:submit(command, id) end
  controller:execute(cell.id, nil, true)
  assert(requests[1].command.code == '%%sql main' and requests[1].command.bootstrap)
  assert(vim.deep_equal(original, vim.api.nvim_buf_get_lines(buf, 0, -1, false)))
  assert(next(editor.history.pending) == nil, 'bootstrap entered history')
  local failure
  controller:execute(cell.id, function(_, err) failure = err end, true)
  assert(failure.reason == 'conflict' and #requests == 1)
  requests[1].callback({ ok = true })
  vim.api.nvim_buf_set_lines(buf, 2, 3, false, { '  ' })
  model:flush()
  controller:execute(cell.id)
  assert(requests[2].command.bootstrap and requests[2].command.code == '%%sql main')
  requests[2].callback(nil, { reason = 'unsupported' })
  vim.api.nvim_buf_set_lines(buf, 1, 3, false, { 'print(42)' })
  model:flush()
  controller:execute(cell.id)
  assert(not requests[3].command.bootstrap and requests[3].command.code == 'print(42)')
  requests[3].callback({ ok = true })
  editor:close()
  vim.api.nvim_buf_delete(buf, { force = true })
end
return M
