local M = {}
local key = '<C-\\><C-\\>'

function M.toggle()
  vim.cmd.stopinsert()
  local buf = require('jusi').toggle_focus()
  if buf and vim.b[buf].jusi_role == 'interactive_terminal' then vim.cmd.startinsert() end
end

function M.attach(buf)
  local owned = {}
  for _, mode in ipairs({ 'n', 'i', 't' }) do
    vim.api.nvim_buf_call(buf, function()
      -- Respect both global and buffer-local user bindings.
      if vim.fn.maparg(key, mode) ~= '' then return end
      local rhs = mode == 't' and '<C-\\><C-n><Cmd>lua require("jusi.focus").toggle()<CR>'
        or '<Cmd>lua require("jusi.focus").toggle()<CR>'
      vim.keymap.set(mode, key, rhs, { buffer = buf, silent = true, desc = 'Jusi: toggle cell/output focus' })
      owned[mode] = vim.fn.maparg(key, mode, false, true).rhs
    end)
  end
  return function()
    if not vim.api.nvim_buf_is_valid(buf) then return end
    vim.api.nvim_buf_call(buf, function()
      for mode, rhs in pairs(owned) do
        local mapping = vim.fn.maparg(key, mode, false, true)
        if mapping.buffer == 1 and mapping.rhs == rhs then vim.keymap.del(mode, key, { buffer = buf }) end
      end
    end)
  end
end
return M
