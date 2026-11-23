local M = {}
function M.run()
  local key = 'jusi_neovim.attachments'
  local previous, notify = package.loaded[key], vim.notify
  local capture, warnings = nil, {}
  package.loaded[key] = { register = function(_, handler) capture = handler; return function() end end }
  vim.notify = function(message) table.insert(warnings, message) end
  local buf = vim.api.nvim_create_buf(true, false)
  local path = vim.fn.tempname()
  local ok, failure = xpcall(function()
    vim.api.nvim_set_current_buf(buf)
    vim.api.nvim_buf_set_name(buf, path)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, {'╭──', '%%fixture', 'text', '╰──'})
    local model = require('jusi.notebook').attach(buf)
    local images = require('jusi.attachments')
    local editor = { model = model, group = vim.api.nvim_create_augroup('image_spec', {}) }
    images.attach(editor)
    model.on_cells_retired = function(ids)
      for _, id in ipairs(ids) do images.clear(editor, id, 'cell replaced') end
    end
    model.on_cells_changed = function(ids)
      for _, id in ipairs(ids) do images.reconcile(editor, id) end
    end
    local id = model:cell_at_row(2).id
    local items = {{ name='one.png', bytes='one', media_type='image/png' }, { name='two.png', bytes='two', media_type='image/png' }}
    capture(2)(items)
    vim.api.nvim_buf_set_lines(buf, 2, 3, false, {'edited'})
    vim.cmd('write')
    vim.cmd('split')
    vim.cmd('close')
    assert(#editor.attachments[id].items == 2)
    assert(not table.concat(vim.fn.readfile(path)):find('png'))
    vim.cmd('JusiClearImages 1')
    assert(editor.attachments[id].items[1].name == 'two.png')
    local sent = vim.list_extend({}, editor.attachments[id].items)
    capture(2)({items[2]}) -- An identical file explicitly attached during the request.
    images.submitted(editor, id, sent, nil, 'rejected')
    images.submitted(editor, id, sent, {operation={outcome='cancelled'}})
    assert(#editor.attachments[id].items == 2)
    images.submitted(editor, id, sent, {operation={outcome='succeeded'}})
    assert(#editor.attachments[id].items == 1 and editor.attachments[id].items[1] ~= sent[1])
    vim.cmd('edit!')
    assert(vim.wait(500, function() return not editor.attachments[id] end))
    assert(table.concat(warnings):find('two.png', 1, true))
    capture(2)(items)
    local pending = capture(2)
    vim.cmd('enew')
    vim.cmd('bunload ' .. buf)
    assert(not next(editor.attachments))
    assert(not pcall(pending, items))
  end, debug.traceback)
  vim.notify, package.loaded[key] = notify, previous
  if vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_delete(buf, {force=true}) end
  vim.fn.delete(path)
  assert(ok, failure)
end
return M
