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

function M.find(buf)
  return valid_windows(buf)[1]
end

function M.show(buf, options)
  local opts = options or {}
  local existing = M.find(buf)
  if existing then
    if opts.enter then
      vim.api.nvim_set_current_win(existing)
    end
    return existing
  end

  local anchor = opts.anchor_buf and M.find(opts.anchor_buf) or nil
  return vim.api.nvim_open_win(buf, opts.enter == true, {
    split = opts.split or "below",
    win = anchor or 0,
    height = opts.height,
  })
end

return M
