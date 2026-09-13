local actions = require("jusi.editor_actions")
local protocol = require("jusi.protocol")
local M = {}
local function read(name)
  local file = assert(io.open("protocol/fixtures/v1/" .. name .. ".json", "r"))
  local data = vim.json.decode(file:read("*a")); file:close(); return data
end
local function text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n") .. (vim.bo[buf].endofline and "\n" or "")
end
function M.run()
  assert(protocol.validate_editor_action(read("valid/editor-show-diff"), "show_diff"))
  assert(protocol.validate_application_action(read("valid/application-diff-begin")))
  assert(protocol.validate_editor_action_fetch(read("valid/editor-diff-fetch")))
  assert(protocol.validate_event(read("valid/editor-diff-requested")))
  assert(protocol.validate_command(read("valid/editor-diff-command"), "editor_action"))
  for _, name in ipairs({ "boundary", "path", "nul", "accept" }) do
    assert(not protocol.validate_editor_action(read("invalid/editor-diff-" .. name), "show_diff"))
  end
  local original_tab = vim.api.nvim_get_current_tabpage()
  local original_wins = vim.api.nvim_tabpage_list_wins(original_tab)
  local original_buf = vim.api.nvim_get_current_buf()
  for _, pair in ipairs({ { "α\n", "β\n" }, { "", "created" }, { "removed\n", "" }, { "same", "same" }, { "a\r\n\n", "b\n" } }) do
    for _, staged in ipairs({ false, true }) do
      local content = { action = "show_diff", text = pair[1] .. pair[2], before_bytes = #pair[1],
        before_name = "before.py", after_name = "after.py", filetype = "python" }
      local path
      if staged then
        path = vim.fn.tempname()
        local file = assert(io.open(path, "wb")); file:write(content.text); file:close()
        content.text = ""
      end
      local result = assert(actions.apply(content, { source_path = path }))
      assert(result.tab ~= original_tab and #vim.api.nvim_list_tabpages() == 2)
      assert(text(result.before_buf) == pair[1] and text(result.after_buf) == pair[2])
      for _, buf in ipairs({ result.before_buf, result.after_buf }) do
        assert(vim.bo[buf].readonly and not vim.bo[buf].modifiable and not vim.bo[buf].modified)
        assert(vim.bo[buf].filetype == "python" and not vim.bo[buf].modeline)
        local wins = vim.fn.win_findbuf(buf)
        assert(#wins == 1 and vim.wo[wins[1]].diff)
      end
      assert(vim.api.nvim_get_current_buf() == result.after_buf)
      assert(vim.deep_equal(vim.api.nvim_tabpage_list_wins(original_tab), original_wins))
      vim.cmd("tabclose")
      assert(vim.api.nvim_get_current_buf() == original_buf)
      vim.api.nvim_buf_delete(result.before_buf, { force = true })
      vim.api.nvim_buf_delete(result.after_buf, { force = true })
      if path then os.remove(path) end
    end
  end
  for _, boundary in ipairs({ 1, 999 }) do
    local invalid = read("valid/editor-show-diff"); invalid.before_bytes = boundary
    assert(not actions.apply(invalid))
    assert(#vim.api.nvim_list_tabpages() == 1 and vim.api.nvim_get_current_buf() == original_buf)
  end
  local open_win = vim.api.nvim_open_win
  vim.api.nvim_open_win = function() error("fixture split failure") end
  local result = actions.apply(read("valid/editor-show-diff"))
  vim.api.nvim_open_win = open_win
  assert(not result and #vim.api.nvim_list_tabpages() == 1 and vim.api.nvim_get_current_buf() == original_buf)
end
return M
