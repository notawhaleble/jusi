local M = {}
local Terminal = {}
Terminal.__index = Terminal

local function valid_buffer(buf)
  return buf and vim.api.nvim_buf_is_valid(buf)
end

function Terminal:write(data)
  if type(data) ~= "string" then
    return false, "terminal output data must be a string"
  end
  if self.closed then
    return false, "terminal surface is closed"
  end
  local ok, message = pcall(vim.api.nvim_chan_send, self.channel, data)
  if not ok then
    return false, tostring(message)
  end
  return true
end

function Terminal:close()
  if self.closed then
    return
  end
  self.closed = true
  pcall(vim.api.nvim_chan_close, self.channel)
  if valid_buffer(self.buf) then
    require("jusi.presentation.window").close_for_buffer(self.buf)
    pcall(vim.api.nvim_buf_delete, self.buf, { force = true })
  end
end

function M.new(options)
  local opts = options or {}
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.api.nvim_buf_set_name(buf, string.format("jusi://output/%s/%s", opts.notebook_id or "notebook", opts.cell_id or "cell"))
  local channel = vim.api.nvim_open_term(buf, {})
  vim.b[buf].jusi_role = "output"
  vim.b[buf].jusi_notebook_id = opts.notebook_id
  vim.b[buf].jusi_cell_id = opts.cell_id
  vim.b[buf].jusi_execution_id = opts.execution_id
  vim.b[buf].jusi_client_id = opts.client_id
  require("jusi.focus").attach(buf)
  return setmetatable({
    buf = buf,
    channel = channel,
    cell_id = opts.cell_id,
    execution_id = opts.execution_id,
    client_id = opts.client_id,
    closed = false,
  }, Terminal)
end

M.Terminal = Terminal

return M
