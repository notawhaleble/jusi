local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local palette = require('jusi.palette')
  local windows = require('jusi.presentation.window')
  local source = vim.api.nvim_get_current_buf()
  local original_tab = vim.api.nvim_get_current_tabpage()
  local nb = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_buf_set_name(nb, '/tmp/jusi palette test.vipynb')
  vim.bo[nb].filetype = 'jusi'
  vim.api.nvim_buf_set_lines(nb, 0, -1, false, {'╭──', '%%sql main', 'old', '╞══', 'history', '╰──'})
  local editor = require('jusi.editing').attach(nb)
  local id = editor.model:ordered_cells()[1].id
  jusi._sessions[nb] = { controller = { plugin_catalog = { plugins = {} }, palette = {sql={entries={'main','other'}}, vd={entries={}}} } }
  local label = 'jusi palette test'
  assert(vim.tbl_contains(palette.complete('', 'J ', 2), 'jusi\\ palette\\ test'))
  local line = 'J jusi\\ palette\\ test '
  assert(vim.deep_equal(palette.complete('', line, #line), {'sql','vd'}))
  line = line .. 'sql m'
  assert(vim.deep_equal(palette.complete('', line, #line), {'main'}))
  vim.cmd.tabnew()
  local remote_tab = vim.api.nvim_get_current_tabpage()
  vim.api.nvim_set_current_buf(nb)
  vim.api.nvim_set_current_tabpage(original_tab)
  vim.api.nvim_set_current_buf(source)
  local source_win = vim.api.nvim_get_current_win()
  vim.cmd('belowright split')
  local lower = vim.api.nvim_create_buf(false,true)
  vim.api.nvim_win_set_buf(0, lower)
  vim.api.nvim_set_current_win(source_win)
  vim.api.nvim_buf_set_lines(source, 0, -1, false, {'α new', 'β new'})
  palette.command({fargs={label,'sql','main'},range=2,line1=1,line2=2,bang=false})
  assert(vim.api.nvim_get_current_tabpage() == original_tab, 'palette jumped to another tab')
  assert(vim.api.nvim_get_current_buf() == nb)
  assert(windows.find(source, original_tab), 'source split was replaced')
  assert(vim.api.nvim_win_get_position(0)[2] < vim.api.nvim_win_get_position(windows.find(source, original_tab))[2], 'notebook did not open on the left')
  local layout = vim.fn.winlayout()
  assert(layout[1] == 'row' and layout[2][1][1] == 'leaf' and layout[2][1][2] == vim.api.nvim_get_current_win(),
    'notebook split was not the leftmost full-height column')
  assert(#editor.model:ordered_cells() == 1 and editor.model:ordered_cells()[1].id == id)
  assert(vim.deep_equal(vim.api.nvim_buf_get_lines(nb,0,-1,false), {'╭──','%%sql main','α new','β new','╞══','history','╰──'}))
  vim.api.nvim_set_current_win(windows.find(source, original_tab))
  vim.api.nvim_buf_set_lines(source, 0, -1, false, {'xαy'})
  vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes('gg0lv<Esc>', true, false, true), 'xt', false)
  palette.command({fargs={label,'sql','main'},range=2,line1=1,line2=1,bang=false})
  assert(vim.api.nvim_buf_get_lines(nb,2,3,false)[1] == 'α', 'visual selection split a Unicode character')
  assert(vim.api.nvim_buf_get_lines(nb,4,5,false)[1] == 'history', 'selection damaged history')
  local submit = jusi.submit
  local submitted
  jusi.submit = function(buf,row) submitted={buf,row} end
  vim.cmd('J! jusi\\ palette\\ test sql main')
  assert(submitted[1] == nb and submitted[2] == 2, 'bang bypassed contextual submit')
  jusi.submit = submit

  local centered = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_buf_set_name(centered, '/tmp/jusi palette centered.vipynb')
  vim.bo[centered].filetype = 'jusi'
  local centered_lines = {'╭──'}
  for index = 1, 30 do table.insert(centered_lines, 'before ' .. index) end
  vim.list_extend(centered_lines, {'╰──', '╭──', '%%sql main', 'target', '╰──', '╭──'})
  for index = 1, 30 do table.insert(centered_lines, 'after ' .. index) end
  table.insert(centered_lines, '╰──')
  vim.api.nvim_buf_set_lines(centered, 0, -1, false, centered_lines)
  local centered_editor = require('jusi.editing').attach(centered)
  jusi._sessions[centered] = { controller = { plugin_catalog = { plugins = {} }, palette = {sql={entries={'main'}}} } }
  palette.command({fargs={'jusi palette centered','sql','main'},range=0,bang=false})
  local middle = math.floor((vim.api.nvim_win_get_height(0) + 1) / 2)
  assert(math.abs(vim.fn.winline() - middle) <= 1, 'palette did not center the selected cell')
  jusi._sessions[centered] = nil
  centered_editor:close()
  windows.close_for_buffer(centered); vim.api.nvim_buf_delete(centered,{force=true})

  palette.command({fargs={label},range=0,bang=false})
  vim.cmd.stopinsert()
  assert(#editor.model:ordered_cells() == 2, 'plain palette did not create a cell')
  -- A visible output in another tab must not steal the local projection.
  local output = vim.api.nvim_create_buf(false,true)
  vim.api.nvim_set_current_tabpage(remote_tab)
  windows.show(output,{anchor_buf=nb,enter=false})
  vim.api.nvim_set_current_tabpage(original_tab)
  local win = windows.show(output,{anchor_buf=nb,enter=false})
  assert(vim.api.nvim_win_get_tabpage(win) == original_tab)
  jusi._sessions[nb] = nil
  editor:close()
  windows.close_for_buffer(output); vim.api.nvim_buf_delete(output,{force=true})
  vim.api.nvim_set_current_tabpage(remote_tab); vim.cmd.tabclose()
  vim.api.nvim_set_current_tabpage(original_tab)
  windows.close_for_buffer(nb); vim.api.nvim_buf_delete(nb,{force=true})
  vim.api.nvim_buf_delete(lower,{force=true})
  vim.api.nvim_set_current_buf(source)
end
return M
