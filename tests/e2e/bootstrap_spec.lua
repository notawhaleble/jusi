local jusi = require('jusi')
local M = {}
local function wait_for(predicate, message) assert(vim.wait(10000, predicate, 10), message) end
function M.run()
  local old_path, old_notify = vim.env.PYTHONPATH, vim.notify
  local notifications, buf, service = {}
  vim.env.PYTHONPATH = vim.fn.getcwd() .. '/tests/fixtures/terminal_plugin' .. (old_path and ':' .. old_path or '')
  vim.notify = function(message) notifications[#notifications + 1] = tostring(message) end
  jusi.setup({ terminal_bridge_command = { '.venv/bin/python', '-m', 'jusi', 'terminal-bridge' } })
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_set_current_buf(buf)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { '╭──', '%%terminal_fixture main', '╰──' })
    service = jusi.start_service({ buf = buf, command = { '.venv/bin/python', '-m', 'jusi', 'serve' }, timeout_ms = 8000 })
    wait_for(function() return jusi._sessions[buf] and jusi._sessions[buf].controller.transport_state == 'connected' end, 'service connection')
    local session = jusi._sessions[buf]
    jusi.start_kernel(buf)
    wait_for(function() return session.controller.kernel_state == 'on' end, 'kernel startup')
    local cell = session.model:cell_at_row(1)
    local done, response, err
    jusi.execute(buf, 1, function(result, failure_) done, response, err = true, result, failure_ end)
    wait_for(function() return done end, 'empty initialization')
    assert(response and not err, vim.inspect(err))
    local id = response.execution.client_id
    wait_for(function() return session.controller.clients[id] and next(session.interactive.surfaces) end, 'bootstrap client and surface')
    local _, surface = next(session.interactive.surfaces)
    wait_for(function() return table.concat(vim.api.nvim_buf_get_lines(surface.buf, 0, -1, false), '\n'):find('initial submission:', 1, true) end, 'application ready')
    assert(#session.model:history(cell.id) == 0, 'initialization recorded a fake turn')
    done = false
    session.controller:complete({ kernel_id = session.controller.kernel_id, notebook_id = session.model.notebook_id,
      cell_id = cell.id, client_id = id, body = '', cursor_pos = 0 }, function(result, failure_) done, response, err = true, result, failure_ end)
    wait_for(function() return done end, 'completion before first submission')
    assert(not err and #response.completion.items == 2, vim.inspect(err))
    vim.api.nvim_buf_set_lines(buf, 2, 2, false, { 'PRIVATE DRAFT MUST NOT BE SENT' })
    session.model:flush()
    local draft = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    done = false
    jusi.bootstrap(buf, 1, function(result, failure_) done, response, err = true, result, failure_ end)
    wait_for(function() return done end, 'idempotent initialization')
    assert(not err and response.client.client_id == id, vim.inspect(err))
    assert(vim.deep_equal(draft, vim.api.nvim_buf_get_lines(buf, 0, -1, false)))
    done = false
    session.controller:close_client(id, function(_, failure_) err, done = failure_, true end)
    wait_for(function() return done and not session.controller.clients[id] end, 'close initialized client')
    assert(not err)
    done = false
    jusi.bootstrap(buf, 1, function(result, failure_) done, response, err = true, result, failure_ end)
    wait_for(function() return done end, 'initialize populated cell')
    assert(not err and response.execution.client_id ~= id, vim.inspect(err))
    assert(vim.deep_equal(draft, vim.api.nvim_buf_get_lines(buf, 0, -1, false)))
    assert(#session.model:history(cell.id) == 0)
    wait_for(function() return next(session.interactive.surfaces) end, 'new surface')
    _, surface = next(session.interactive.surfaces)
    wait_for(function() return table.concat(vim.api.nvim_buf_get_lines(surface.buf, 0, -1, false), '\n'):find('initial submission:', 1, true) end, 'new application ready')
    assert(not table.concat(vim.api.nvim_buf_get_lines(surface.buf, 0, -1, false), '\n'):find('PRIVATE DRAFT', 1, true))
  end, debug.traceback)
  if buf and jusi._sessions[buf] then jusi._destroy_session(buf)
  elseif service and service.state ~= 'stopped' then service:stop() end
  vim.env.PYTHONPATH, vim.notify = old_path, old_notify
  if not ok then error(failure .. '\n' .. vim.inspect(notifications)) end
  print('Plugin bootstrap and pre-turn completion passed')
end
return M
