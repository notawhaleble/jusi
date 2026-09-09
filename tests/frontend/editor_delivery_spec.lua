local protocol = require("jusi.protocol")
local Delivery = require("jusi.editor_delivery")
local M = {}
local function read(name)
  local f = assert(io.open("protocol/fixtures/v1/" .. name, "r"))
  local data = vim.json.decode(f:read("*a")); f:close(); return data
end
function M.run()
  for _, name in ipairs({ "application-editor-action", "application-editor-result" }) do
    assert(protocol.validate_application_action(read("valid/" .. name .. ".json")))
  end
  for _, name in ipairs({ "application-action-wrong-kind", "application-action-path" }) do
    assert(not protocol.validate_application_action(read("invalid/" .. name .. ".json")))
  end
  assert(protocol.validate_command(read("valid/ack-editor-action.json"), "ack_editor_action"))
  assert(protocol.validate_event(read("valid/editor-action-requested.json")))
  assert(not protocol.validate_event(read("invalid/editor-action-wrong-owner.json")))
  for _, spec in ipairs({
    { protocol.validate_editor_action_ack, "editor-action-ack", { "editor-action-ack-outcome" } },
    { protocol.validate_editor_action_fetch, "editor-action-fetch", { "editor-action-fetch-expiry", "editor-action-fetch-kind" } },
    { protocol.validate_terminal_stream_control, "terminal-stream-editor-attach", { "terminal-stream-editor-id" } },
    { protocol.validate_health_response, "health-editor-action", { "health-editor-action-owner" } },
  }) do
    assert(spec[1](read("valid/" .. spec[2] .. ".json")))
    for _, name in ipairs(spec[3]) do assert(not spec[1](read("invalid/" .. name .. ".json"))) end
  end
  assert(not protocol.validate_command(read("invalid/ack-editor-action-outcome.json"), "ack_editor_action"))
  local response = read("valid/editor-action-fetch.json")
  local action = response.action
  local requests, acks, applied = {}, {}, 0
  local alive, lost_ack = true, nil
  local c = { editor_id = action.editor_id, supervisor_id = "sup_fixture", transport_state = "connected",
    clients = { [action.client_id] = { runtime_id = action.runtime_id, cell_id = action.cell_id } },
    notebook = { notebook_id = action.notebook_id, cell_by_id = function() return alive and {} or nil end },
    transport = { request = function(_, method, path, _, _, callback)
      assert(method == "GET"); table.insert(requests, { path = path, callback = callback })
    end },
    _command = function(_, kind, fields) fields.kind = kind; return fields end,
    _request = function(_, _, _, command, callback) table.insert(acks, command); if command.action_id ~= lost_ack then callback({ ok = true, delivery = { action_id = command.action_id, outcome = command.outcome, reason = "" } }) end end,
  }
  local delivery = Delivery.new(c, function(content) applied = applied + 1; assert(content.text == response.content.text); return true end)
  local other = vim.deepcopy(action); other.editor_id = "editor_other"
  delivery:request(other); assert(#requests == 0, "another editor's action was fetched")
  delivery:request(action); delivery:request(action)
  assert(#requests == 1, "duplicate event created parallel fetches")
  requests[1].callback(response)
  assert(applied == 1 and acks[1].outcome == "delivered")
  delivery:request(action)
  assert(applied == 1 and #requests == 1 and #acks == 2, "ack replay repeated the side effect")
  local later = vim.deepcopy(response); later.action.action_id = "act_later"
  delivery:request(later.action)
  c.transport_state = "disconnected"
  requests[2].callback(later)
  assert(applied == 1, "disconnected editor applied a late reply")
  c.transport_state = "connected"
  delivery:request(later.action)
  alive = false
  requests[3].callback(later)
  assert(applied == 1 and acks[#acks].outcome == "failed", "retired source still delivered")
  alive = true
  local expired = vim.deepcopy(response); expired.action.action_id = "act_expired"; expired.remaining_ms = 0
  delivery:request(expired.action); requests[4].callback(expired)
  assert(applied == 1 and acks[#acks].outcome == "failed", "expired fetch was applied")
  -- A lost acknowledgment must survive pressure from later completed actions.
  lost_ack = "act_unconfirmed"
  local unconfirmed = vim.deepcopy(response); unconfirmed.action.action_id = lost_ack
  delivery:request(unconfirmed.action); requests[#requests].callback(unconfirmed)
  for index = 1, 260 do
    local next_response = vim.deepcopy(response); next_response.action.action_id = "act_many_" .. index
    delivery:request(next_response.action); requests[#requests].callback(next_response)
  end
  local before = applied
  delivery:request(unconfirmed.action)
  assert(applied == before and delivery.seen[lost_ack].outcome == "delivered", "unconfirmed delivery was evicted")

end
return M
