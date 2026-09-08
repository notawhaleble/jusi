local notebook = require("jusi.notebook")
local lifecycle_module = require("jusi.cell_lifecycle")
local M = {}
function M.run()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "a", "╰──", "╭──", "b", "╰──" })
  local model = notebook.attach(buf)
  local a, b = model:ordered_cells()[1].id, model:ordered_cells()[2].id
  local calls, failures, interrupt_reply = {}, {}, nil
  local controller = {
    executions = { exe_a = { cell_id = a, outcome = "succeeded" }, exe_b = { cell_id = b, outcome = "running" } },
    clients = { cli_b = { cell_id = b }, cli_a = { cell_id = a } },
    interrupt = function(_, id, cb) table.insert(calls, { "interrupt", id }); interrupt_reply = cb end,
    close_client = function(_, id, cb) table.insert(calls, { "close_client", id }); cb({ ok = true }) end,
  }
  local removed = {}
  local lifecycle = lifecycle_module.new({ model = model, controller = controller,
    presentation = { retire_execution = function(_, cell, execution) removed[execution] = cell end,
      close_cell = function(_, cell) removed[cell] = true end },
    on_failure = function(failure) table.insert(failures, failure) end })
  model.on_cells_retired = function(ids) for _, id in ipairs(ids) do lifecycle:retire(id) end end
  vim.cmd("let &ul = &ul")
  vim.api.nvim_win_set_cursor(0, { 3, 0 })
  vim.cmd("normal! Vjd")
  assert(#calls == 0, "typing callback performed resource work")
  assert(model:ordered_cells()[1].id == a and model:cell_by_id(b) == nil)
  assert(#calls == 0, "query performed resource work")
  vim.cmd("silent undo")
  local c = model:ordered_cells()[2].id
  assert(c ~= b and model:ordered_cells()[1].id == a)
  assert(vim.wait(1000, function() return interrupt_reply ~= nil end))
  assert(#calls == 1 and calls[1][2] == "exe_b")
  assert(removed.exe_b == b and not removed[a] and not removed.exe_a)
  -- A new client can arrive while interrupt is in flight; cleanup must discover it.
  controller.clients.cli_late = { cell_id = b }
  lifecycle:retire(b)
  assert(vim.wait(1000, function() return lifecycle.again[b] == true end))
  interrupt_reply({ ok = true })
  assert(vim.wait(1000, function() return #calls == 3 end))
  assert(calls[2][2] == "cli_b" and calls[3][2] == "cli_late")
  assert(not lifecycle:accepts(b) and lifecycle:accepts(c))
  assert(not removed[c] and not removed[a])
  lifecycle:detach()
  lifecycle:retire(c)
  assert(#calls == 3, "retired runtime must not start more cleanup")
  model:detach()
  vim.api.nvim_buf_delete(buf, { force = true })

  -- Delete an entire undo-created middle cell: its opener can vanish from
  -- Neovim's index, but the model must still retire its output and split.
  local live_buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(live_buf)
  vim.api.nvim_buf_set_lines(live_buf, 0, -1, false, {
    "╭──", "a", "╰──", "╭──", "b", "╰──", "╭──", "c", "╰──",
  })
  local live_model = notebook.attach(live_buf)
  vim.cmd("let &ul = &ul")
  vim.api.nvim_buf_set_lines(live_buf, 3, 6, false, {})
  live_model:flush()
  vim.cmd("silent undo")
  live_model:flush()
  local owner = live_model:ordered_cells()[2].id
  local presentation = require("jusi.presentation").new({ notebook_id = live_model.notebook_id, notebook_buf = live_buf })
  presentation:start_execution(owner, { execution_id = "exe_deleted" })
  local surface = presentation:write(owner, { execution_id = "exe_deleted", media_type = "text/plain", data = "result" })
  local output_win = vim.fn.bufwinid(surface.buf)
  assert(output_win ~= -1)
  local live_lifecycle = lifecycle_module.new({ model = live_model, presentation = presentation,
    controller = { executions = {}, clients = {} } })
  live_model.on_cells_retired = function(ids) for _, id in ipairs(ids) do live_lifecycle:retire(id) end end
  vim.api.nvim_buf_set_lines(live_buf, 3, 6, false, {})
  assert(vim.wait(1000, function() return live_lifecycle.retired[owner] and surface.closed end))
  assert(not vim.api.nvim_buf_is_valid(surface.buf) and not vim.api.nvim_win_is_valid(output_win))
  assert(#live_model:ordered_cells() == 2)
  live_lifecycle:detach()
  presentation:close()
  live_model:detach()
  vim.api.nvim_buf_delete(live_buf, { force = true })

  -- Failed cleanup remains retryable on reconciliation; other resources still close.
  local attempts = 0
  local retry = lifecycle_module.new({ model = { notebook_id = "nb", cell_by_id = function() return nil end },
    controller = { executions = {}, clients = { cli = { cell_id = "gone", notebook_id = "nb" } },
      close_client = function(_, _, cb)
        attempts = attempts + 1
        cb(nil, attempts == 1 and { reason = "unreachable" } or nil)
      end },
    presentation = { close_cell = function() end, retire_execution = function() end },
    on_failure = function(failure) table.insert(failures, failure) end })
  retry:retire("gone")
  assert(vim.wait(1000, function() return attempts == 1 end))
  assert(#failures == 1 and failures[1].reason == "unreachable")
  retry:reconcile()
  assert(vim.wait(1000, function() return attempts == 2 end))
  assert(retry.clients_closed.cli)
  retry:detach()
end
return M
