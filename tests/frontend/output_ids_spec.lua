local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local original = vim.api.nvim_get_current_buf()
  local buf = vim.api.nvim_create_buf(false,true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf,0,-1,false,{'╭──','first','╰──','╭──','second','╰──'})
  local editor = require('jusi.editing').attach(buf)
  local cells = editor.model:ordered_cells()
  local presentation = require('jusi.presentation').new({notebook_id=editor.model.notebook_id,notebook_buf=buf})
  local closed = {}
  local session = {buf=buf,model=editor.model,presentation=presentation,
    interactive={buffer_for_cell=function() end},controller={kernel_state='off'},
    lifecycle={close_cell=function(_,id) closed[#closed+1]=id; presentation:close_cell(id); return true end}}
  jusi._sessions[buf] = session
  local function output(cell,execution)
    return presentation:write(cell.id,{media_type='text/plain',data='hello',execution_id=execution})
  end
  local a = output(cells[1],'exec_a')
  local id = vim.b[a.buf].jusi_output_number
  vim.api.nvim_win_set_cursor(0,{5,0})
  editor.cellmode:set(true)
  local function keys(value) vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(value,true,false,true),'xt',false) end
  keys(tostring(id)..'G')
  assert(vim.api.nvim_win_get_cursor(0)[1]==2)
  vim.api.nvim_win_set_cursor(0,{5,0})
  keys(tostring(id)..'Q')
  assert(closed[1]==cells[1].id and vim.api.nvim_win_get_cursor(0)[1]==5)
  local replacement = output(cells[1],'exec_b')
  local replacement_id = vim.b[replacement.buf].jusi_output_number
  assert(replacement_id > id)
  local notify = vim.notify; vim.notify=function() end
  jusi.close_number(id)
  vim.notify=notify
  assert(#closed==1 and vim.api.nvim_buf_is_valid(replacement.buf),'stale number closed replacement')
  editor.cellmode:set(false)
  keys(tostring(replacement_id)..'\\g')
  assert(vim.api.nvim_win_get_cursor(0)[1]==2)
  keys(tostring(replacement_id)..'\\q')
  assert(#closed==2)
  vim.keymap.set('n','\\b',function() end,{buffer=buf})
  editor:close()
  assert(vim.fn.maparg('\\b','n')~='', 'detach removed user replacement')
  assert(vim.fn.maparg('\\j','n')=='', 'detach left owned mapping')
  presentation:close(); jusi._sessions[buf]=nil
  vim.api.nvim_set_current_buf(original); vim.api.nvim_buf_delete(buf,{force=true})
end
return M
