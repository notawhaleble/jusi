local protocol = require("jusi.protocol")
local M = {}

-- All content has arrived and passed protocol validation before destination
-- mutation. No remote path, Ex command, or source-client lifetime is inherited.
function M.apply(result, opts)
  opts = opts or {}
  local valid, err = protocol.validate_editor_action(result, result and result.action)
  if not valid then return nil, err end
  if result.action == "copy" then
    local register = opts.register or '"'
    if #register ~= 1 or not register:match('^[a-z0-9"+*]$') then return nil, "unsupported destination register" end
    vim.fn.setreg(register, result.text, result.regtype)
    if register == '"' then vim.fn.setreg("0", result.text, result.regtype) end
    return true
  end
  local buf = vim.api.nvim_create_buf(true, false)
  local ok, failure = pcall(function()
    vim.bo[buf].modeline = false
    vim.bo[buf].swapfile = false
    vim.bo[buf].bufhidden = "hide"
    vim.api.nvim_buf_set_name(buf, vim.fn.tempname() .. "--" .. result.name)
    local lines = vim.split(result.text, "\n", { plain = true })
    local eol = result.text:sub(-1) == "\n"
    if eol then table.remove(lines) end
    if #lines == 0 then lines = { "" } end
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    vim.bo[buf].endofline = eol
    vim.bo[buf].fixendofline = false
    vim.bo[buf].filetype = result.filetype
    vim.bo[buf].modified = true
    vim.b[buf].jusi_export_name = result.name
    if opts.show ~= false then
      local win = opts.win
      if not win or not vim.api.nvim_win_is_valid(win) then win = vim.api.nvim_get_current_win() end
      vim.api.nvim_open_win(buf, true, { split = "below", win = win })
    end
  end)
  if not ok then
    vim.api.nvim_buf_delete(buf, { force = true })
    return nil, tostring(failure)
  end
  return buf
end

return M
