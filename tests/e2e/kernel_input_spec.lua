local jusi = require("jusi")
local M = {}
local function wait_for(predicate, message)
  assert(vim.wait(8000, predicate, 10), message)
end

function M.run()
  local old_notify = vim.notify
  local notifications = {}
  vim.notify = function(message) table.insert(notifications, message) end
  local buf, service, session
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "input('lalala: ')", "╰──" })
    vim.api.nvim_win_set_buf(0, buf)
    local notebook_win = vim.api.nvim_get_current_win()
    service = jusi.start_service({ buf = buf, command = { ".venv/bin/python", "-m", "jusi", "serve" } })
    wait_for(function()
      session = jusi._sessions[buf]
      return session and session.controller.transport_state == "connected"
    end, "input scenario service did not connect")
    jusi.start_kernel(buf)
    wait_for(function() return session.controller.kernel_state == "on" end, "input scenario kernel did not start")
    local controller = session.controller
    local cell_id = session.model:cell_at_row(1).id
    local events = {}
    controller.on_event = function(event) table.insert(events, event) end
    local function pending()
      wait_for(function() return controller.pending_input ~= nil end, "kernel did not publish input request: " .. vim.inspect(notifications))
      local request = controller.pending_input
      assert(session.marks.records[request.cell_id].state == "busy", "pending input must remain blue/busy")
      return request
    end
    local function complete(execution_id, outcome)
      wait_for(function() return controller.executions[execution_id].outcome ~= "running" end, "input execution did not complete")
      assert(controller.executions[execution_id].outcome == outcome, vim.inspect(notifications))
      assert(controller.pending_input == nil)
      assert(controller.kernel_state == "on")
      local execution = controller.executions[execution_id]
      if session.model:cell_by_id(execution.cell_id) then
        local record = session.marks.records[execution.cell_id]
        if record and record.execution_id == execution_id then
          assert(record.state == (outcome == "succeeded" and "done" or "interrupted"), "execution mark did not settle")
        end
      end
    end
    jusi.execute(buf, 1)
    local first = pending()
    assert(first.prompt == "lalala: " and first.cell_id == cell_id)
    local output_buf = assert(session.presentation:buffer_for_cell(cell_id))
    wait_for(function()
      return vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1]:match("^lalala: ") ~= nil
    end, "input prompt was not rendered")
    assert(vim.fn.bufwinid(output_buf) ~= -1, "input request must reveal its output split")
    assert(vim.api.nvim_get_current_win() == notebook_win and vim.api.nvim_get_current_buf() == buf, "prompt stole focus")

    -- Inspect-before-replay reconnect retains the exact pending request.
    controller:close()
    controller:connect()
    wait_for(function() return controller.transport_state == "connected" end, "input reconnect failed")
    assert(controller.pending_input.input_request_id == first.input_request_id)
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "ololo" })
    vim.api.nvim_win_set_cursor(notebook_win, { 2, 0 })
    vim.cmd("JusiInput")
    complete(first.execution_id, "succeeded")
    wait_for(function()
      return table.concat(vim.api.nvim_buf_get_lines(output_buf, 0, -1, false), "\n"):find("'ololo'", 1, true) ~= nil
    end, "kernel did not receive literal ololo input")
    assert(vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1] == "lalala: ololo", "input echo must be literal and separate from expression result")
    assert(vim.api.nvim_buf_get_lines(output_buf, 1, 2, false)[1] == "'ololo'", "genuine Python result must remain intact")
    assert(session.model:cell_at_row(1).id == cell_id)
    local starts = 0
    for _, event in ipairs(events) do if event.kind == "execution.started" then starts = starts + 1 end end
    assert(starts == 1, "input submission created a new execution")
    assert(session.presentation:buffer_for_cell(cell_id) == output_buf, "input replaced the artifact")

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "a = input('assigned: ')" })
    jusi.execute(buf, 1)
    local assigned = pending()
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "ololo" })
    jusi.input(buf, 1)
    complete(assigned.execution_id, "succeeded")
    local assigned_buf = assert(session.presentation:buffer_for_cell(cell_id))
    wait_for(function() return vim.api.nvim_buf_get_lines(assigned_buf, 0, 1, false)[1] == "assigned: ololo" end,
      "assignment must visibly echo accepted input even without an expression result")

    -- Two prompts in one execution get separate identities. Empty/multiline input is literal.
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "a = input('first: '); b = input('second: '); print(repr((a, b)))" })
    jusi.execute(buf, 1)
    local second = pending()
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "" })
    jusi.input(buf, 1)
    wait_for(function() return controller.pending_input and controller.pending_input.input_request_id ~= second.input_request_id end, "second prompt missing")
    local third = controller.pending_input
    assert(third.execution_id == second.execution_id)
    local stale_done, stale_failure
    controller:_request("POST", "/v1/kernels/" .. third.kernel_id .. "/executions/" .. third.execution_id .. "/input",
      controller:_command("submit_input", { kernel_id = third.kernel_id, execution_id = third.execution_id,
        input_request_id = second.input_request_id, value = "stale" }), function(_, err) stale_failure = err; stale_done = true end)
    wait_for(function() return stale_done end, "stale reply did not complete")
    assert(stale_failure and stale_failure.reason == "conflict")
    assert(controller.pending_input.input_request_id == third.input_request_id)
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "  α  ", "β" })
    jusi.input(assert(session.presentation:buffer_for_cell(cell_id)), 999)
    complete(second.execution_id, "succeeded")
    output_buf = assert(session.presentation:buffer_for_cell(cell_id))
    wait_for(function()
      return table.concat(vim.api.nvim_buf_get_lines(output_buf, 0, -1, false), "\n"):find("('', '  α  \\nβ')", 1, true) ~= nil
    end, "empty/Unicode/multiline input did not round trip")

    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "input('interrupt: ')" })
    jusi.execute(buf, 1)
    local interrupted = pending()
    jusi.interrupt(buf, 1)
    complete(interrupted.execution_id, "interrupted")
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "1 + 1" })
    local response, execute_failure
    controller:execute(cell_id, function(value, err) response = value; execute_failure = err end)
    wait_for(function() return response or execute_failure end, "execution after input interrupt did not finish")
    assert(response and response.execution.outcome == "succeeded", vim.inspect(execute_failure))
    -- A damaged closing boundary invalidates text, not execution ownership.
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "input('structure: ')" })
    jusi.execute(buf, 1)
    local structural = pending()
    local structural_buf = assert(session.presentation:buffer_for_cell(cell_id))
    vim.api.nvim_set_current_win(notebook_win)
    vim.cmd("let &ul = &ul")
    vim.api.nvim_win_set_cursor(notebook_win, { 2, 0 })
    vim.cmd("normal! J")
    assert(session.model:cell_at_row(1).id == cell_id)
    assert(not session.model:cell_snapshot(cell_id).valid)
    assert(session.presentation:buffer_for_cell(cell_id) == structural_buf)
    assert(controller.pending_input.input_request_id == structural.input_request_id)
    jusi.toggle_focus(buf, 1)
    assert(vim.api.nvim_get_current_buf() == structural_buf)
    jusi.toggle_focus(structural_buf)
    assert(vim.api.nvim_get_current_buf() == buf)
    vim.cmd("silent undo")
    assert(session.model:cell_at_row(1).id == cell_id)
    assert(session.model:cell_snapshot(cell_id).valid)
    assert(session.presentation:buffer_for_cell(cell_id) == structural_buf)
    vim.cmd("silent redo")
    jusi.close(buf, 1) -- control remains usable even before repairing the text
    complete(structural.execution_id, "interrupted")
    wait_for(function() return session.presentation:buffer_for_cell(cell_id) == nil end,
      "malformed cell lost access to pending-input cleanup")
    vim.cmd("silent undo")
    assert(session.model:cell_at_row(1).id == cell_id)

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "input('close: ')" })
    jusi.execute(buf, 1)
    local abandoned = pending()
    local abandoned_buf = assert(session.presentation:buffer_for_cell(cell_id))
    local window = vim.fn.bufwinid(abandoned_buf)
    vim.api.nvim_win_close(window, true)
    assert(controller.pending_input.input_request_id == abandoned.input_request_id, "native window close must only hide")
    jusi.close(abandoned_buf, 999)
    complete(abandoned.execution_id, "interrupted")
    wait_for(function() return session.presentation:buffer_for_cell(cell_id) == nil end, "JusiClose did not retire input artifact")
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "1 + 1" })
    local after_close, after_close_failure
    controller:execute(cell_id, function(value, err) after_close = value; after_close_failure = err end)
    wait_for(function() return after_close or after_close_failure end, "execution hung after closing pending input")
    assert(after_close and after_close.execution.outcome == "succeeded", vim.inspect(after_close_failure))

    -- Merging A and B retires B's runtime; undo restores fresh C, never B.
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "40 + 2" })
    vim.api.nvim_buf_set_lines(buf, 3, 3, false, { "╭──", "input('merge: ')", "╰──" })
    local b_id = session.model:cell_at_row(4).id
    local a_done
    controller:execute(cell_id, function(value) a_done = value end)
    wait_for(function() return a_done ~= nil end, "merge A setup failed")
    local a_output = assert(session.presentation:buffer_for_cell(cell_id))
    jusi.execute(buf, 4)
    local b_input = pending()
    local b_output = assert(session.presentation:buffer_for_cell(b_id))
    local b_output_window = vim.fn.bufwinid(b_output)
    assert(b_output_window ~= -1)
    vim.api.nvim_set_current_buf(buf)
    vim.cmd("let &ul = &ul")
    vim.api.nvim_win_set_cursor(0, { 3, 0 })
    vim.cmd("normal! Vjd")
    assert(session.model:ordered_cells()[1].id == cell_id)
    assert(session.model:cell_by_id(b_id) == nil)
    vim.cmd("silent undo") -- undo before asynchronous close completes
    local c_id = session.model:cell_at_row(4).id
    assert(c_id ~= b_id)
    complete(b_input.execution_id, "interrupted")
    wait_for(function() return not vim.api.nvim_buf_is_valid(b_output) end, "retired B output survived merge")
    assert(not vim.api.nvim_win_is_valid(b_output_window), "retired B output window survived merge")
    assert(session.presentation:buffer_for_cell(cell_id) == a_output)
    assert(session.presentation:buffer_for_cell(c_id) == nil)
    local c_done, c_failure
    vim.api.nvim_buf_set_lines(buf, 4, 5, false, { "6 * 7" })
    controller:execute(c_id, function(value, err) c_done = value; c_failure = err end)
    wait_for(function() return c_done or c_failure end, "fresh C execution hung after merge")
    assert(c_done and c_done.execution.outcome == "succeeded", vim.inspect(c_failure))
    local c_output = assert(session.presentation:buffer_for_cell(c_id))
    -- Reverse roles: active A survives; completed C is destroyed by the same merge.
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "input('keep A: ')" })
    jusi.execute(buf, 1)
    local a_input = pending()
    a_output = assert(session.presentation:buffer_for_cell(cell_id))
    vim.cmd("let &ul = &ul")
    vim.api.nvim_win_set_cursor(0, { 3, 0 })
    vim.cmd("normal! Vjd")
    wait_for(function() return not vim.api.nvim_buf_is_valid(c_output) end, "completed merged output survived")
    assert(controller.pending_input.input_request_id == a_input.input_request_id)
    assert(session.presentation:buffer_for_cell(cell_id) == a_output)
    jusi.interrupt(buf, 1)
    complete(a_input.execution_id, "interrupted")
    vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "1 + 1" })

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "input('stop: ')" })
    jusi.execute(buf, 1)
    pending()
    jusi.stop_kernel(buf)
    wait_for(function() return controller.kernel_state == "off" end, "stop waited behind pending input")
    assert(controller.pending_input == nil)
    jusi.start_kernel(buf)
    wait_for(function() return controller.kernel_state == "on" end, "kernel did not start after stopping input")
    jusi.execute(buf, 1)
    pending()
    local old_runtime = controller.runtime_id
    jusi.restart(buf)
    wait_for(function() return controller.runtime_id ~= old_runtime and controller.kernel_state == "on" end,
      "restart waited behind pending input")
    assert(controller.pending_input == nil)
    jusi.stop_service(buf)
    wait_for(function() return service.state == "stopped" end, "input scenario cleanup failed")
  end, debug.traceback)
  if buf and jusi._sessions[buf] then jusi._destroy_session(buf)
  elseif service and service.state ~= "stopped" then service:stop() end
  vim.notify = old_notify
  if not ok then error(failure .. "\nnotifications: " .. vim.inspect(notifications) .. "\nservice stderr: " .. (service and service.stderr or "")) end
  print("Kernel input cell submission passed")
end
return M
