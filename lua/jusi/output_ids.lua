-- Short editor-local handles; never derived from opaque backend identities.
local M = {}
local next_id, buffers = 0, {}
function M.attach(buf)
  if vim.b[buf].jusi_output_number then return vim.b[buf].jusi_output_number end
  next_id = next_id + 1
  local id = next_id
  buffers[id] = buf
  vim.b[buf].jusi_output_number = id
  vim.api.nvim_create_autocmd('BufWipeout', { buffer = buf, once = true, callback = function() buffers[id] = nil end })
  vim.api.nvim_buf_call(buf, function()
    for key, callback in pairs({ G = function() require('jusi').goto_number(vim.v.count) end,
        Q = function() require('jusi').close_number(vim.v.count) end }) do
      if vim.fn.maparg(key, 'n') == '' then vim.keymap.set('n', key, callback, { buffer = buf, silent = true }) end
    end
  end)
  return id
end
function M.buffer(id)
  local buf = buffers[id]
  if buf and vim.api.nvim_buf_is_valid(buf) and vim.b[buf].jusi_output_number == id then return buf end
end
return M
