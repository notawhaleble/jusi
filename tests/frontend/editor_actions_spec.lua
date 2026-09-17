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
    assert(vim.bo[buf].modifiable and not vim.bo[buf].modified and not vim.bo[buf].modeline)
    assert(vim.bo[buf].buftype == "nofile" and not vim.bo[buf].buflisted and not vim.bo[buf].swapfile)
    assert(vim.fn.filereadable(vim.api.nvim_buf_get_name(buf)) == 0, "open wrote a file")
    vim.api.nvim_buf_delete(buf, { force = true })
    local path = vim.fn.tempname()
    local file = assert(io.open(path, "wb")); file:write(text); file:close()
    local staged = assert(actions.apply({ action = "open", text = "", name = "staged.txt", filetype = "text" },
      { show = false, source_path = path }))
    local staged_text = table.concat(vim.api.nvim_buf_get_lines(staged, 0, -1, false), "\n") .. (vim.bo[staged].endofline and "\n" or "")
    assert(staged_text == text, "staged open changed newlines")
    vim.api.nvim_buf_delete(staged, { force = true })
    os.remove(path)
  end
  local disposable = assert(actions.apply(
    { action = "open", text = "temporary", name = "disposable.txt", filetype = "text" }, { show = false }))
  local win = vim.api.nvim_open_win(disposable, true, { split = "below", win = vim.api.nvim_get_current_win() })
  vim.api.nvim_buf_set_lines(disposable, 0, -1, false, { "changed locally" })
  assert(not vim.bo[disposable].modified, "scratch edit became an unsaved file change")
  vim.cmd("close")
  assert(not vim.api.nvim_win_is_valid(win), "plain :close did not close edited scratch export")
  vim.api.nvim_buf_delete(disposable, { force = true })
  assert(actions.apply({ action = "copy", text = string.rep("α", 262145), regtype = "v" }, { register = "a" }))
  vim.fn.setreg("a", saved)
end
return M
