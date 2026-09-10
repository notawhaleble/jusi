local M = {}
local fields = { kind = true, command = true, base_url = true, kernel_name = true,
  terminal_bridge_command = true, timeout_ms = true }
local function argv(value)
  assert(type(value) == "table" and #value > 0, "target command must be a nonempty argv list")
  for key, item in pairs(value) do
    assert(type(key) == "number" and key >= 1 and key <= #value and key % 1 == 0
      and type(item) == "string" and item ~= "" and not item:find("%z"), "invalid target command argument")
  end
end
function M.validate(targets)
  assert(type(targets) == "table", "targets must be an alias table")
  for alias, target in pairs(targets) do
    assert(type(alias) == "string" and alias:match("^[%w_-]+$"), "invalid target alias")
    assert(type(target) == "table", "target must be a table")
    for key in pairs(target) do assert(fields[key], "unknown target option: " .. tostring(key)) end
    assert(target.kind == "local" or target.kind == "remote", "target kind must be local or remote")
    if target.kind == "remote" then
      assert(type(target.base_url) == "string" and target.base_url:match("^https?://[^%s]+$"), "remote target requires an HTTP(S) service URL")
      assert(target.command == nil, "remote target cannot launch a frontend-local service")
    else
      assert(target.base_url == nil, "local target obtains its URL from its owned service")
      if target.command then argv(target.command) end
    end
    if target.terminal_bridge_command then argv(target.terminal_bridge_command) end
    if target.kernel_name then assert(type(target.kernel_name) == "string" and target.kernel_name ~= "", "invalid kernel name") end
    if target.timeout_ms then assert(type(target.timeout_ms) == "number" and target.timeout_ms > 0, "invalid target timeout") end
  end
  return targets
end
return M
