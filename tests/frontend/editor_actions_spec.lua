local protocol = require("jusi.protocol")
local actions = require("jusi.editor_actions")
local M = {}
local function read(name)
  local f = assert(io.open("protocol/fixtures/v1/" .. name, "r"))
  local result = vim.json.decode(f:read("*a")); f:close(); return result
end
function M.run()
  assert(protocol.validate_health_response(read("valid/health-client-operation.json")))
  assert(not protocol.validate_health_response(read("invalid/health-operation-wrong-client.json")))
  assert(not protocol.validate_plugin_worker_message(read("invalid/worker-interrupt-missing-target.json")))
  for name, kind in pairs({ ["editor-action"] = "editor_action", ["interrupt-client"] = "interrupt_client" }) do
    assert(protocol.validate_command(read("valid/" .. name .. ".json"), kind))
  end
  for _, name in ipairs({ "worker-rejected", "worker-interrupt" }) do
    assert(protocol.validate_plugin_worker_message(read("valid/" .. name .. ".json")))
  end
  assert(not protocol.validate_command(read("invalid/editor-action-path.json"), "editor_action"))
  assert(not protocol.validate_command(read("invalid/interrupt-client-missing-operation.json"), "interrupt_client"))
  assert(not protocol.validate_editor_action(read("invalid/editor-open-path.json"), "open"))
  assert(not protocol.validate_editor_action(read("invalid/editor-copy-nul.json"), "copy"))
  local saved = vim.fn.getreginfo("a")
  assert(actions.apply(read("valid/editor-copy.json"), { register = "a" }))
  assert(vim.fn.getreg("a") == "  α\tβ\n\n" and vim.fn.getregtype("a") == "V")
  local previous = vim.fn.getreg("a")
  assert(not actions.apply(read("invalid/editor-copy-nul.json"), { register = "a" }))
  assert(vim.fn.getreg("a") == previous, "failed copy mutated register")
  for _, text in ipairs({ "", "\n", "a\n\n", "  α\tb", "x\r\ny\n" }) do
    assert(actions.apply({ action = "copy", text = text, regtype = "v" }, { register = "a" }))
    assert(vim.fn.getreg("a") == text)
    local buf = assert(actions.apply({ action = "open", text = text, name = "data.txt", filetype = "text" }, { show = false }))
    local reconstructed = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n") .. (vim.bo[buf].endofline and "\n" or "")
    assert(reconstructed == text, vim.inspect({ reconstructed, text }))
    assert(vim.bo[buf].modifiable and vim.bo[buf].modified and not vim.bo[buf].modeline)
    assert(vim.fn.filereadable(vim.api.nvim_buf_get_name(buf)) == 0, "open wrote a file")
    vim.api.nvim_buf_delete(buf, { force = true })
  end
  assert(not actions.apply({ action = "copy", text = string.rep("α", 262145), regtype = "v" }))
  vim.fn.setreg("a", saved)
end
return M
