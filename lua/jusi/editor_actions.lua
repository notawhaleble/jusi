local protocol = require("jusi.protocol")
local M = {}

local function show_diff(result, opts)
  local before, after
  if opts.source_path then
    local file, err = io.open(opts.source_path, "rb")
    if not file then return nil, err end
    local size = file:seek("end")
    if not size or result.before_bytes > size then file:close(); return nil, "diff boundary exceeds content" end
    file:seek("set", result.before_bytes)
    local next_char = file:read(1)
    if next_char and next_char:byte() >= 128 and next_char:byte() < 192 then
      file:close(); return nil, "diff boundary splits UTF-8"
    end
    file:seek("set", 0)
    before = result.before_bytes == 0 and "" or file:read(result.before_bytes)
    after = file:read("*a")
    file:close()
    if not before or not after then return nil, "could not read diff snapshots" end
  else
    if result.before_bytes > #result.text then return nil, "diff boundary exceeds content" end
    local byte = result.text:byte(result.before_bytes + 1)
    if byte and byte >= 128 and byte < 192 then return nil, "diff boundary splits UTF-8" end
    before, after = result.text:sub(1, result.before_bytes), result.text:sub(result.before_bytes + 1)
  end
  local original_tab, original_win = vim.api.nvim_get_current_tabpage(), vim.api.nvim_get_current_win()
  local buffers, tab = {}, nil
  local ok, failure = pcall(function()
    for index, text in ipairs({ before, after }) do
      local buf, err = M.apply({ action = "open", text = text, filetype = result.filetype,
        name = index == 1 and result.before_name or result.after_name }, { show = false })
      if not buf then error(err) end
      buffers[index] = buf
      vim.bo[buf].modified = false
      vim.bo[buf].readonly = true
      vim.bo[buf].modifiable = false
      vim.b[buf].jusi_diff_side = index == 1 and "before" or "after"
    end
    vim.cmd("tab split")
    tab = vim.api.nvim_get_current_tabpage()
    local left = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(left, buffers[1])
    local right = vim.api.nvim_open_win(buffers[2], true, { split = "right", win = left })
    for _, win in ipairs({ left, right }) do
      vim.api.nvim_win_call(win, function() vim.cmd("diffthis") end)
    end
  end)
  if not ok then
    if tab and tab ~= original_tab and vim.api.nvim_tabpage_is_valid(tab) then
      vim.api.nvim_set_current_tabpage(tab)
      vim.cmd("tabclose!")
    end
    for _, buf in ipairs(buffers) do
      if vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_delete(buf, { force = true }) end
    end
    if vim.api.nvim_win_is_valid(original_win) then vim.api.nvim_set_current_win(original_win) end
    return nil, tostring(failure)
  end
  return { before_buf = buffers[1], after_buf = buffers[2], tab = tab }
end

-- All content has arrived and passed protocol validation before destination
-- mutation. No remote path, Ex command, or source-client lifetime is inherited.
function M.apply(result, opts)
  opts = opts or {}
  local valid, err = protocol.validate_editor_action(result, result and result.action)
  if not valid then return nil, err end
  if result.action == "show_diff" then return show_diff(result, opts) end
  if opts.source_path and result.action == "copy" then
    local file = assert(io.open(opts.source_path, "rb"))
    result = vim.tbl_extend("force", result, { text = file:read("*a") })
    file:close()
  end
  if result.action == "copy" then
    local register = opts.register or '"'
    if #register ~= 1 or not register:match('^[a-z0-9"+*]$') then return nil, "unsupported destination register" end
    vim.fn.setreg(register, result.text, result.regtype)
    if register == '"' then vim.fn.setreg("0", result.text, result.regtype) end
    return true
  end
  -- Open actions deliver disposable snapshots, not files or edit sessions.
  -- A scratch buffer stays modifiable without acquiring a modified flag, so
  -- native close/quit never asks the user to save content that has no
  -- writeback destination.
  local buf = vim.api.nvim_create_buf(false, true)
  local ok, failure = pcall(function()
    vim.bo[buf].modeline = false
    vim.bo[buf].swapfile = false
    vim.bo[buf].bufhidden = "hide"
    vim.api.nvim_buf_set_name(buf, vim.fn.tempname() .. "--" .. result.name)
    local lines = opts.source_path and vim.fn.readfile(opts.source_path, "b")
      or vim.split(result.text, "\n", { plain = true })
    local eol = opts.source_path and #lines > 1 and lines[#lines] == "" or result.text:sub(-1) == "\n"
    if eol then table.remove(lines) end
    if #lines == 0 then lines = { "" } end
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    vim.bo[buf].endofline = eol
    vim.bo[buf].fixendofline = false
    vim.bo[buf].filetype = result.filetype
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
