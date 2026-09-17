local M = {}

local function valid_windows(buf)
  local result = {}
  for _, window in ipairs(vim.fn.win_findbuf(buf)) do
    if vim.api.nvim_win_is_valid(window) then
      table.insert(result, window)
    end
  end
  return result
end

function M.close_for_buffer(buf)
  for _, window in ipairs(valid_windows(buf)) do
    -- Window IDs are not ownership: users may have switched a former output
    -- split to another buffer, including during a window-close autocmd.
    if vim.api.nvim_win_is_valid(window) and vim.api.nvim_win_get_buf(window) == buf then
      -- Neovim cannot close its final window. The caller still deletes the
      -- output buffer, allowing Neovim to replace it in that remaining window.
      pcall(vim.api.nvim_win_close, window, true)
    end
  end
end

function M.find(buf, tab)
  local windows = valid_windows(buf)
  for _, window in ipairs(windows) do
    if vim.api.nvim_win_get_tabpage(window) == (tab or vim.api.nvim_get_current_tabpage()) then return window end
  end
  if not tab then return windows[1] end
end

local function prepare_terminal_window(win, buf)
  local role = vim.b[buf].jusi_role
  require("jusi.statusline").refresh(win)
  if role ~= "output" and role ~= "interactive_terminal" then return end
  -- A newly split window inherits notebook gutters. Clear them before a PTY
  -- is created, so the application's first draw uses its full text width.
  for name, value in pairs({ number = false, relativenumber = false, signcolumn = "no", foldcolumn = "0", statuscolumn = "" }) do
    vim.api.nvim_set_option_value(name, value, { win = win })
  end
end

function M.show(buf, options)
  local opts = options or {}
  local anchor = opts.anchor_buf and M.find(opts.anchor_buf) or nil
  local tab = opts.tab or (anchor and vim.api.nvim_win_get_tabpage(anchor)) or vim.api.nvim_get_current_tabpage()
  local existing = M.find(buf, tab)
  if existing then
    prepare_terminal_window(existing, buf)
    if opts.enter then vim.api.nvim_set_current_win(existing) end
    return existing
  end
  if not anchor or vim.api.nvim_win_get_tabpage(anchor) ~= tab then
    anchor = vim.api.nvim_tabpage_get_win(tab)
  end
  local win
  if opts.fullheight then
    win = vim.api.nvim_win_call(anchor, function()
      vim.cmd('topleft vertical sbuffer ' .. buf)
      return vim.api.nvim_get_current_win()
    end)
    if opts.enter then vim.api.nvim_set_current_win(win) end
  else
    win = vim.api.nvim_open_win(buf, opts.enter == true, {
      split = opts.split or "below",
      win = anchor,
      height = opts.height,
    })
  end
  prepare_terminal_window(win, buf)
  return win
end

return M
