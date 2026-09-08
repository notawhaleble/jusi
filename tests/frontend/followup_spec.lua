local protocol = require("jusi.protocol")
local notebook = require("jusi.notebook")
local controller_module = require("jusi.controller")
local M = {}
local function read(name)
  local file = assert(io.open("protocol/fixtures/v1/" .. name, "r"))
  local value = vim.json.decode(file:read("*a"))
  file:close()
  return value
end
function M.run()
  local scenario = read("scenarios/client-followup.json")
  assert(protocol.validate_command(read("valid/followup.json"), "followup"))
  for _, event in ipairs(scenario.events) do assert(protocol.validate_event(event)) end
  for _, name in ipairs({ "followup-missing-client", "followup-nonstring-body" }) do
    assert(not protocol.validate_command(read("invalid/" .. name .. ".json"), "followup"))
  end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "  literal body  ", "α", "╰──" })
  local model = notebook.attach(buf)
  local cell_id = model:cell_at_row(1).id
  local requests = {}
  local controller = controller_module.new({ notebook = model, transport = {
    request = function(_, method, path, command, options, callback)
      table.insert(requests, { method = method, path = path, command = command, callback = callback })
      return requests[#requests]
    end,
  } })
  local failure
  controller:followup(cell_id, function(_, err) failure = err end)
  assert(failure and #requests == 0, "regular cells cannot submit a plugin followup")
  local client = { client_id = "client_existing", notebook_id = model.notebook_id,
    cell_id = cell_id, capabilities = { "execute", "followup" } }
  controller.clients[client.client_id] = client
  controller:followup(cell_id)
  assert(requests[1].command.body == scenario.command.body)
  assert(requests[1].command.client_id == client.client_id)
  assert(requests[1].path == "/v1/clients/client_existing/followups" and requests[1].method == "POST")
  assert(next(controller.executions) == nil, "followup created a kernel execution")
  controller:followup(cell_id, function(_, err) failure = err end)
  assert(failure and #requests == 1, "duplicate in-flight submission was accepted")
  requests[1].callback({ ok = true })
  vim.api.nvim_buf_set_lines(buf, 1, 3, false, { "" })
  controller:followup(cell_id)
  assert(requests[2].command.body == "", "empty followup was rejected")
  requests[2].callback(nil, { reason = "plugin_error" })
  client.capabilities = { "execute" }
  controller:followup(cell_id, function(_, err) failure = err end)
  assert(failure and #requests == 2)
  client.capabilities = { "followup" }
  vim.api.nvim_buf_set_lines(buf, 2, 3, false, { "broken closer" })
  controller:followup(cell_id, function(_, err) failure = err end)
  assert(failure and #requests == 2, "malformed cells must not submit a body")
  model:detach()
  vim.api.nvim_buf_delete(buf, { force = true })
end
return M
