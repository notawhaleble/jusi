local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")
local presentation_module = require("jusi.presentation")
local transport_module = require("jusi.transport.http_sse")

local M = {}

local function wait_for(timeout_ms, predicate, message)
  assert(vim.wait(timeout_ms, predicate, 10), message)
end

local function start_service()
  local state = { ready = nil, stdout = "", stderr = "" }
  local process = vim.system({
    ".venv/bin/python",
    "-m",
    "jusi",
    "serve",
    "--host",
    "127.0.0.1",
    "--port",
    "0",
  }, {
    text = true,
    stdout = function(_, data)
      if not data then
        return
      end
      state.stdout = state.stdout .. data
      local newline = state.stdout:find("\n", 1, true)
      if newline and not state.ready then
        local line = state.stdout:sub(1, newline - 1)
        vim.schedule(function()
          state.ready = vim.json.decode(line)
        end)
      end
    end,
    stderr = function(_, data)
      state.stderr = state.stderr .. (data or "")
    end,
  })
  if not vim.wait(8000, function()
    return state.ready ~= nil
  end, 10) then
    pcall(process.kill, process, 15)
    process:wait(3000)
    error("service did not become ready:\n" .. state.stderr)
  end
  return process, state
end

local function run_scenario()
  local service, service_state = start_service()
  local model
  local controller
  local presentation
  local ok, result = xpcall(function()
    local ready = service_state.ready
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
    assert(controller.event_sequence == 12)
    assert(#failures == 0, vim.inspect(failures))
    return { event_count = #events, output_count = #outputs }
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
  pcall(service.kill, service, 15)
  local service_result = service:wait(5000)
  if not ok then
    error(result .. "\nservice stderr:\n" .. service_state.stderr)
  end
  assert(service_result.code == 0 or service_result.signal == 15, vim.inspect(service_result))
  return result
end

function M.run()
  local result = run_scenario()
  print("Lua service walking skeleton " .. vim.json.encode(result))
end

return M
