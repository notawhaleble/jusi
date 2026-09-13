local M = {}
function M.run()
  local focus = require('jusi.focus')
  local buf = vim.api.nvim_create_buf(false,true)
  local key = '<C-\\><C-\\>'
  local original = vim.api.nvim_get_current_buf()
  vim.api.nvim_set_current_buf(buf)
  vim.keymap.set('i',key,'user insert',{buffer=buf})
  local detach = focus.attach(buf)
  assert(vim.fn.maparg(key,'i') == 'user insert')
  assert(vim.fn.maparg(key,'n'):find('jusi.focus',1,true))
  assert(vim.fn.maparg(key,'t'):lower():find('<c-n>',1,true))
  vim.keymap.set('n',key,'user replacement',{buffer=buf})
  detach()
  assert(vim.fn.maparg(key,'n') == 'user replacement')
  assert(vim.fn.maparg(key,'t') == '')
  vim.api.nvim_set_current_buf(original)
  vim.api.nvim_buf_delete(buf,{force=true})
end
return M
