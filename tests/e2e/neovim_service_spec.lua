local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")
local presentation_module = require("jusi.presentation")
local local_service = require("jusi.service.local")
local transport_module = require("jusi.transport.http_sse")

local M = {}

local function wait_for(timeout_ms, predicate, message)
  assert(vim.wait(timeout_ms, predicate, 10), message)
end

local function start_service()
  local started
  local failure
  local service = local_service.start({
    command = { ".venv/bin/python", "-m", "jusi", "serve" },
  }, function(value, start_failure)
    started = value
    failure = start_failure
  end)
  if not vim.wait(8000, function()
    return started ~= nil or failure ~= nil
  end, 10) then
    service:stop()
    error("service launcher did not complete")
  end
  assert(failure == nil, vim.inspect(failure))
  return service
end

local function run_scenario()
  local service = start_service()
  local model
  local controller
  local presentation
  local ok, result = xpcall(function()
    local ready = service.ready
    local buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "1 + 1", "╰──" })
    model = notebook.attach(buf)
    local cell_id = model:ordered_cells()[1].id
    local transport = transport_module.new({ base_url = string.format("http://%s:%d", ready.host, ready.port) })
    local outputs = {}
    local events = {}
    local failures = {}
    presentation = presentation_module.new({ notebook_id = model.notebook_id })
    local presentation_callbacks = presentation:controller_callbacks()
    controller = controller_module.new({
      notebook = model,
      transport = transport,
      on_event = function(event)
        table.insert(events, event)
      end,
      on_execution_started = presentation_callbacks.on_execution_started,
      on_output = function(output_cell_id, output)
        presentation_callbacks.on_output(output_cell_id, output)
        table.insert(outputs, { cell_id = output_cell_id, output = output })
      end,
      on_failure = function(failure)
        table.insert(failures, failure)
      end,
    })

    controller:connect()
    wait_for(5000, function()
      return controller.transport_state == "connected"
    end, "frontend event transport did not connect: " .. vim.inspect({
      state = controller.transport_state,
      supervisor_id = controller.supervisor_id,
      event_sequence = controller.event_sequence,
      has_event_connection = controller.event_connection ~= nil,
      last_failure = controller.last_transport_failure,
      failures = failures,
    }))

    local start_response
    local start_failure
    controller:start_kernel(function(response, failure)
      start_response = response
      start_failure = failure
    end)
    wait_for(8000, function()
      return start_response ~= nil or start_failure ~= nil
    end, "kernel start did not complete")
    assert(start_failure == nil, vim.inspect(start_failure))
    assert(start_response.kernel.state == "on")

    local execute_response
    local execute_failure
    controller:execute(cell_id, function(response, failure)
      execute_response = response
      execute_failure = failure
    end)
    wait_for(8000, function()
      return execute_response ~= nil or execute_failure ~= nil
    end, "execution did not complete")
    assert(execute_failure == nil, vim.inspect(execute_failure))
    assert(execute_response.execution.outcome == "succeeded")
    wait_for(5000, function()
      return #outputs > 0
    end, "ordered output event did not arrive")
    assert(outputs[1].cell_id == cell_id)
    assert(outputs[1].output.media_type == "text/plain")
    assert(outputs[1].output.data == "2")
    local output_buf = assert(presentation:buffer_for_cell(cell_id))
    wait_for(1000, function()
      return vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1] == "2"
    end, "native terminal did not render the result")
    assert(vim.bo[output_buf].buftype == "terminal")

    vim.api.nvim_set_current_buf(buf)
    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "import time; time.sleep(30)" })
    local interrupted_response
    local interrupted_failure
    controller:execute(cell_id, function(response, failure)
      interrupted_response = response
      interrupted_failure = failure
    end)
    local active_execution_id
    wait_for(5000, function()
      for execution_id, execution in pairs(controller.executions) do
        if execution.cell_id == cell_id and execution.outcome == "running" then
          active_execution_id = execution_id
          return true
        end
      end
      return false
    end, "long execution did not publish its active identity")
    local interrupt_response
    local interrupt_failure
    controller:interrupt(active_execution_id, function(response, failure)
      interrupt_response = response
      interrupt_failure = failure
    end)
    wait_for(5000, function()
      return interrupt_response ~= nil or interrupt_failure ~= nil
    end, "interrupt control request did not complete while execution was active")
    assert(interrupt_failure == nil, vim.inspect(interrupt_failure))
    assert(interrupt_response.interrupt.result == "requested")
    wait_for(5000, function()
      return interrupted_response ~= nil or interrupted_failure ~= nil
    end, "interrupted execution did not complete")
    assert(interrupted_failure == nil, vim.inspect(interrupted_failure))
    assert(interrupted_response.execution.outcome == "interrupted")
    assert(controller.executions[active_execution_id].outcome == "interrupted")
    assert(controller.kernel_state == "on", "interrupt changed authoritative kernel state")

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "jusi_restart_probe = 41" })
    local probe_response
    local probe_failure
    controller:execute(cell_id, function(response, failure)
      probe_response = response
      probe_failure = failure
    end)
    wait_for(8000, function()
      return probe_response ~= nil or probe_failure ~= nil
    end, "restart probe setup did not complete")
    assert(probe_failure == nil, vim.inspect(probe_failure))
    assert(probe_response.execution.outcome == "succeeded")

    local old_runtime_id = controller.runtime_id
    local old_discovery_id = controller.discovery_id
    local old_kernel_id = controller.kernel_id
    local old_notebook_id = model.notebook_id
    local text_before_restart = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    local undo_sequence_before_restart = vim.fn.undotree().seq_cur
    local next_notebook_id = notebook.new_notebook_id()
    local restart_response
    local restart_failure
    controller:restart_notebook(next_notebook_id, function(response, failure)
      restart_response = response
      restart_failure = failure
    end)
    wait_for(12000, function()
      return restart_response ~= nil or restart_failure ~= nil
    end, "full notebook restart did not complete")
    assert(restart_failure == nil, vim.inspect(restart_failure))
    assert(restart_response.cleanup.result == "stopped")
    assert(controller.runtime_id ~= old_runtime_id)
    assert(controller.discovery_id ~= old_discovery_id)
    assert(controller.kernel_id ~= old_kernel_id)
    assert(restart_response.runtime.notebook_id == next_notebook_id)

    presentation:close()
    model:detach()
    model = notebook.attach(buf, { notebook_id = next_notebook_id })
    presentation = presentation_module.new({ notebook_id = model.notebook_id })
    presentation_callbacks = presentation:controller_callbacks()
    controller.notebook = model
    controller.on_execution_started = presentation_callbacks.on_execution_started
    controller.executions = {}
    local new_cell_id = model:ordered_cells()[1].id
    assert(model.notebook_id ~= old_notebook_id)
    assert(new_cell_id ~= cell_id)
    assert(vim.api.nvim_buf_get_lines(buf, 0, -1, false)[2] == "jusi_restart_probe = 41")
    assert(vim.deep_equal(vim.api.nvim_buf_get_lines(buf, 0, -1, false), text_before_restart))
    assert(vim.fn.undotree().seq_cur == undo_sequence_before_restart)
    assert(not vim.api.nvim_buf_is_valid(output_buf), "restart must retire old output surfaces")

    vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "int('jusi_restart_probe' in globals())" })
    local post_restart_response
    local post_restart_failure
    controller:execute(new_cell_id, function(response, failure)
      post_restart_response = response
      post_restart_failure = failure
    end)
    wait_for(8000, function()
      return post_restart_response ~= nil or post_restart_failure ~= nil
    end, "post-restart execution did not complete")
    assert(post_restart_failure == nil, vim.inspect(post_restart_failure))
    wait_for(5000, function()
      return #outputs > 1
    end, "post-restart result event did not arrive")
    assert(outputs[#outputs].cell_id == new_cell_id)
    assert(outputs[#outputs].output.data == "0", "new kernel unexpectedly retained old globals")

    local stop_response
    local stop_failure
    controller:stop_kernel(function(response, failure)
      stop_response = response
      stop_failure = failure
    end)
    wait_for(8000, function()
      return stop_response ~= nil or stop_failure ~= nil
    end, "kernel stop did not complete")
    assert(stop_failure == nil, vim.inspect(stop_failure))
    assert(stop_response.cleanup.result == "stopped")
    wait_for(5000, function()
      return controller.kernel_state == "off"
    end, "authoritative off event did not arrive")

    for index, event in ipairs(events) do
      assert(event.sequence == index + 1, "frontend observed an event sequence gap")
    end
    assert(controller.event_sequence == #events + 1)
    assert(#failures == 0, vim.inspect(failures))
    return { event_count = #events, output_count = #outputs, full_restart = true }
  end, debug.traceback)

  if controller then
    controller:close()
  end
  if presentation then
    presentation:close()
  end
  if model then
    model:detach()
  end
  local stop_result
  local stop_failure
  service:stop(function(result_value, failure_value)
    stop_result = result_value
    stop_failure = failure_value
  end)
  vim.wait(5000, function()
    return stop_result ~= nil
  end, 10)
  if not ok then
    error(result .. "\nservice stderr:\n" .. service.stderr)
  end
  assert(stop_result ~= nil, "local service did not stop")
  assert(stop_failure == nil, vim.inspect(stop_failure))
  return result
end

function M.run()
  local result = run_scenario()
  print("Lua service walking skeleton " .. vim.json.encode(result))
end

return M
