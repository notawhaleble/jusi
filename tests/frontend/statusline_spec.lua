local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local status = require('jusi.statusline')
  local original = vim.api.nvim_get_current_buf()
  local win = vim.api.nvim_get_current_win()
  local saved = vim.api.nvim_get_option_value('statusline', {win=win,scope='local'})
  vim.api.nvim_set_option_value('statusline', 'user status', {win=win,scope='local'})
  local buf = vim.api.nvim_create_buf(false,true)
  vim.api.nvim_buf_set_name(buf, '/tmp/100% notebook.vipynb')
  vim.api.nvim_set_current_buf(buf)
  vim.bo[buf].filetype = 'jusi'; status.refresh(win)
  local ctl = {supervisor_id='sup_test',kernel_state='on',transport_state='connected'}
  jusi._sessions[buf] = {buf=buf,model={notebook_id='nb_status'},controller=ctl,target_alias='local'}
  local function rendered()
    return vim.api.nvim_eval_statusline(status.render(win), {winid=win,maxwidth=200}).str
  end
  assert(rendered():find('100%% notebook.vipynb') and rendered():find('kernel: on',1,true))
  ctl.transport_state='disconnected'
  assert(rendered():find('on (last known)',1,true) and rendered():find('transport: disconnected',1,true))
  ctl.transport_state='connected'; ctl.kernel_state='off'
  assert(rendered():find('kernel: off',1,true) and not rendered():find('last known',1,true))
  local client = require('jusi.presentation.terminal').new({notebook_id='nb_status',cell_id='cell_status',execution_id='exec_status'})
  local cw = require('jusi.presentation.window').show(client.buf,{anchor_buf=buf,enter=false})
  local id = vim.b[client.buf].jusi_output_number
  vim.g.statusline_winid = cw
  assert(status.render():find('output '..id,1,true), 'inactive window used notebook context')
  vim.g.statusline_winid = nil
  vim.cmd.colorscheme('default')
  assert(rendered():find('kernel: off',1,true))
  client:close()
  jusi._sessions[buf] = nil
  vim.api.nvim_set_current_win(win); vim.api.nvim_set_current_buf(original); status.refresh(win)
  assert(vim.api.nvim_get_option_value('statusline',{win=win,scope='local'}) == 'user status')
  vim.api.nvim_set_option_value('statusline',saved,{win=win,scope='local'})
  vim.api.nvim_buf_delete(buf,{force=true})
end
return M
