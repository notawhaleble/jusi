local protocol = require("jusi.protocol")
local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")

local M = {}
local function read(name)
  local file = assert(io.open("protocol/fixtures/v1/" .. name, "r"))
  local value = vim.json.decode(file:read("*a"))
  file:close()
  return value
end

function M.run()
  local scenario = read("scenarios/kernel-input.json")
  assert(protocol.validate_command(scenario.command, "submit_input"))
  for _, value in ipairs({ "", "  ololo  ", "α\nβ" }) do
    local command = vim.deepcopy(scenario.command)
    command.value = value
    assert(protocol.validate_command(command, "submit_input"))
  end
  for _, event in ipairs(scenario.events) do assert(protocol.validate_event(event)) end
  assert(protocol.validate_health_response(read("valid/health-pending-input.json")))
  assert(not protocol.validate_health_response(read("invalid/health-input-wrong-execution.json")))
  assert(not protocol.validate_command(read("invalid/input-command-missing-identity.json"), "submit_input"))
  for _, name in ipairs({ "input-request-missing-identity", "input-request-invalid-password", "input-reply-exposes-value" }) do
    assert(not protocol.validate_event(read("invalid/" .. name .. ".json")))
  end

  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", scenario.code, "╰──", "╭──", "other", "╰──" })
  local model = notebook.attach(buf)
  local cell_id = model:cell_at_row(1).id
  local requests = {}
  local transport = { request = function(_, method, path, payload, options, callback)
    table.insert(requests, { method = method, path = path, payload = payload, callback = callback })
    return requests[#requests]
  end }
  local prompts = {}
  local echoes = {}
  local controller = controller_module.new({ notebook = model, transport = transport,
    on_input_requested = function(cell, request) table.insert(prompts, { cell, request }) end,
    on_input_replied = function(cell, request, value) table.insert(echoes, { cell, request, value }) end })
  local health = read("valid/health-pending-input.json")
  health.runtime.notebook_id = model.notebook_id
  health.kernel.notebook_id = model.notebook_id
  health.executions[1].notebook_id = model.notebook_id
  health.executions[1].cell_id = cell_id
  health.pending_input.notebook_id = model.notebook_id
  health.pending_input.cell_id = cell_id
  assert(controller:_accept_health_snapshot(health))
  assert(#prompts == 1 and prompts[1][1] == cell_id, "inspection must recover pending input and prompt")
  local request_id = controller.pending_input.input_request_id
  local failure
  controller:submit_input(model:cell_at_row(4).id, function(_, err) failure = err end)
  assert(failure and #requests == 0, "other cell cannot answer this prompt")
  vim.api.nvim_buf_set_lines(buf, 1, 2, false, { "  ololo  ", "α" })
  controller:submit_input(cell_id)
  assert(requests[1].payload.value == "  ololo  \nα", "input must be literal body text")
  assert(requests[1].payload.input_request_id == request_id)
  assert(requests[1].payload.kind == "submit_input")
  assert(requests[1].path:match("/input$"))
  assert(#echoes == 0, "submission alone is not acceptance")
  local duplicate_failure
  controller:submit_input(cell_id, function(_, err) duplicate_failure = err end)
  assert(duplicate_failure and #requests == 1, "duplicate in-flight submission must not overwrite reply echo")
  local next_request = vim.deepcopy(controller.pending_input)
  next_request.input_request_id = "inp_next"
  controller.pending_input = next_request
  requests[1].callback({ ok = true })
  assert(controller.pending_input == next_request, "old HTTP response must not clear next prompt")
  assert(#echoes == 0, "HTTP acceptance must not reorder echo ahead of preceding SSE output")
  local accepted = read("valid/execution-input-replied.json")
  accepted.supervisor_id = controller.supervisor_id
  accepted.sequence = controller.event_sequence + 1
  accepted.trace_id = requests[1].payload.trace_id
  accepted.payload.execution_id = health.pending_input.execution_id
  accepted.payload.input_request_id = request_id
  accepted.resource.id = accepted.payload.execution_id
  controller:_on_event(accepted)
  assert(#echoes == 1 and echoes[1][3] == "  ololo  \nα", "accepted input must echo literal text")
  controller:_on_event(accepted)
  assert(#echoes == 1, "replay must not duplicate input echo")
  assert(controller.pending_input == next_request, "late acknowledgement must not clear next prompt")
  assert(next(controller.input_submissions) == nil, "accepted reply text must be released")
  controller.pending_input = nil
  failure = nil
  controller:submit_input(cell_id, function(_, err) failure = err end)
  assert(failure and #requests == 1, "no prompt must not fall through to execute or followup")
  model:detach()
  vim.api.nvim_buf_delete(buf, { force = true })
end

return M
