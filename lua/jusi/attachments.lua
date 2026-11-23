local M = {}
local ns = vim.api.nvim_create_namespace('jusi_cell_attachments')
local limit = 16 * 1024 * 1024
local function names(files)
  return table.concat(vim.tbl_map(function(file) return file.name end, files), ', ')
end

function M.clear(editor, id, reason)
  local record = (editor.attachments or {})[id]
  if not record then return end
  editor.attachments[id] = nil
  if vim.api.nvim_buf_is_valid(editor.model.buf) then
    vim.api.nvim_buf_del_extmark(editor.model.buf, ns, record.mark)
    vim.api.nvim_buf_del_extmark(editor.model.buf, ns, record.finish)
  end
  if reason then vim.notify(('Cell %s lost %s (%s)'):format(id, names(record.items), reason), vim.log.levels.WARN) end
end

-- A snapshot retains object identity, including when equal files are reattached.
function M.submitted(editor, id, sent, response, failure)
  if failure or not response or not response.operation or response.operation.outcome ~= 'succeeded' then return end
  local record = editor.attachments and editor.attachments[id]
  if not record or not sent then return end
  local consumed = {}
  for _, item in ipairs(sent) do consumed[item] = true end
  local remaining = vim.tbl_filter(function(item) return not consumed[item] end, record.items)
  if #remaining == #record.items then return end
  if #remaining == 0 then M.clear(editor, id); return end
  local snapshot = editor.model:cell_snapshot(id)
  if not snapshot then return end
  record.items = remaining
  vim.api.nvim_buf_set_extmark(editor.model.buf, ns, snapshot.open_row, 0,
    { id = record.mark, virt_text = {{ names(remaining), 'Special' }} })
end

function M.reconcile(editor, id)
  local record = (editor.attachments or {})[id]
  if not record then return end
  local snapshot = editor.model:cell_snapshot(id)
  local anchor = vim.api.nvim_buf_get_extmark_by_id(editor.model.buf, ns, record.finish, { details = true })
  if not snapshot or not snapshot.valid or snapshot.close_row ~= anchor[1] or (anchor[3] and anchor[3].invalid) then
    M.clear(editor, id, 'cell boundaries changed')
  end
end

local function release_all(editor, reason)
  editor.attachment_generation = (editor.attachment_generation or 0) + 1
  for id in pairs(editor.attachments or {}) do M.clear(editor, id, reason) end
end

function M.close(editor)
  release_all(editor, 'notebook detached')
  if editor.unregister_attachments then editor.unregister_attachments(); editor.unregister_attachments = nil end
end

function M.attach(editor)
  local ok, host = pcall(require, 'jusi_neovim.attachments')
  if not ok then return end
  local buf = editor.model.buf
  editor.attachments = {}
  editor.unregister_attachments = host.register(buf, function(row)
    local cell = assert(editor.model:cell_at_row(row), 'Choose a notebook cell')
    local snapshot = editor.model:cell_snapshot(cell)
    assert(snapshot and snapshot.valid, 'Choose a structurally valid cell')
    local catalog = editor.controller and editor.controller.plugin_catalog
    local accepted = {}
    if catalog then
      local header = vim.api.nvim_buf_get_lines(buf, snapshot.body_start_row, snapshot.body_start_row + 1, false)[1] or ''
      local magic = header:match('^%s*%%%%([%a][%w_-]*)')

      for _, plugin in ipairs(catalog.plugins or {}) do
        for _, family in ipairs(plugin.families) do
          if family.magic_name == magic then
            for _, capability in ipairs(family.capabilities) do
              accepted[capability] = true
            end
          end
        end
      end
    end
    local revision, generation = cell.header_revision, editor.attachment_generation
    return function(files)
      assert(editor.attachment_generation == generation and editor.unregister_attachments and not editor.closed
        and editor.model:cell_by_id(cell.id) == cell
        and editor.model:cell_snapshot(cell).valid and revision == cell.header_revision,
        'Cell changed during attachment capture')
      for _, file in ipairs(files) do
        assert(type(file.media_type) == 'string' and #file.media_type <= 122
          and file.media_type:match('^[a-z0-9][a-z0-9.+%-]*/[a-z0-9][a-z0-9.+%-]*$')
          and (not catalog or accepted['input:' .. file.media_type]),
          'Cell provider does not accept ' .. tostring(file.media_type))
      end
      local old = editor.attachments[cell.id]
      local items = vim.list_extend({}, old and old.items or {})
      for _, file in ipairs(files) do
        table.insert(items, { name = file.name, media_type = file.media_type, bytes = file.bytes })
      end
      local size = 0
      for _, file in ipairs(items) do size = size + #file.bytes end
      assert(#items <= 8 and size <= limit, 'A cell accepts up to eight files totaling 16 MiB')
      local mark = vim.api.nvim_buf_set_extmark(buf, ns, editor.model:cell_snapshot(cell).open_row, 0,
        { id = old and old.mark, virt_text = {{ names(items), 'Special' }} })
      local finish = vim.api.nvim_buf_set_extmark(buf, ns, editor.model:cell_snapshot(cell).close_row, 0,
        { id = old and old.finish, end_col = 1, invalidate = true })
      editor.attachments[cell.id] = { items = items, mark = mark, finish = finish }
    end
  end)
  local function clear_command(command)
    local cell = editor.model:cell_at_row(vim.api.nvim_win_get_cursor(0)[1] - 1)
    local record = cell and editor.attachments[cell.id]
    if not record then return end
    if command.args == '' then M.clear(editor, cell.id); return end
    local index = tonumber(command.args)
    assert(index and index % 1 == 0 and index >= 1 and index <= #record.items, 'Choose an attachment by its one-based index')
    table.remove(record.items, index)
    if #record.items == 0 then M.clear(editor, cell.id); return end
    vim.api.nvim_buf_set_extmark(buf, ns, editor.model:cell_snapshot(cell).open_row, 0,
      { id = record.mark, virt_text = {{ names(record.items), 'Special' }} })
  end
  for _, name in ipairs({'JusiClearAttachments', 'JusiClearImages'}) do
    vim.api.nvim_buf_create_user_command(buf, name, clear_command, { nargs = '?' })
  end
  vim.api.nvim_create_autocmd('BufUnload', { buffer = buf, group = editor.group, callback = function() release_all(editor, 'buffer unloaded') end })
end

return M
