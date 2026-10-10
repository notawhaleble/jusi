local M = {}
local function read(name)
  local f = assert(io.open('protocol/fixtures/v1/' .. name .. '.json', 'r'))
  local value = vim.json.decode(f:read('*a')); f:close(); return value
end
function M.run()
  local protocol = require('jusi.protocol')
  for _, case in ipairs({
    { protocol.validate_attention, 'attention', { 'attention-control-message', 'attention-kind', 'attention-revision' } },
    { protocol.validate_attention_request, 'attention-request', { 'attention-request-owner', 'attention-update-missing-id' } },
    { protocol.validate_attention_request, 'attention-update', {} },
    { protocol.validate_attention_request, 'attention-clear', {} },
    { protocol.validate_application_action, 'application-attention', {} },
    { protocol.validate_application_action, 'application-attention-result', {} },
    { protocol.validate_attention_ack, 'attention-ack', {} },
    { function(v) return protocol.validate_command(v, 'dismiss_attention') end, 'dismiss-attention', { 'dismiss-attention-revision' } },
    { protocol.validate_event, 'attention-event', { 'attention-event-owner' } },
    { protocol.validate_health_response, 'health-attention', { 'health-attention-owner', 'health-attention-duplicate' } },
  }) do
    assert(case[1](read('valid/' .. case[2])), case[2])
    for _, name in ipairs(case[3]) do assert(not case[1](read('invalid/' .. name)), name) end
  end
  local scenario = read('scenarios/attention-lifecycle')
  local attention = require('jusi.attention')
  local tabline = require('jusi.attention_tabline')
  local original_tabline, original_showtabline = vim.o.tabline, vim.o.showtabline
  vim.o.tabline, vim.o.showtabline = '', 0
  local messages, bells, hooks, requests = {}, 0, 0, {}
  local notify = vim.notify
  vim.notify = function(message) messages[#messages+1] = message end
  attention.setup({ bell = function() bells = bells+1 end, notify = function(items, context)
    assert(#items > 0 and context.focus_known); hooks = hooks+1
  end })
  vim.cmd('tabnew')
  local notebook = vim.api.nvim_get_current_buf()
  local original_tab = vim.api.nvim_get_current_tabpage()
  local client_buf = vim.api.nvim_create_buf(false, true)
  local item = scenario.pending
  local controller = { editor_id = item.editor_id, transport_state = 'connected',
    clients = { [item.client_id] = { runtime_id = item.runtime_id, cell_id = item.cell_id } },
    _command = function(_, kind, fields)
      local command = vim.tbl_extend('force', fields, { kind = kind, protocol_version = 1, command_id = 'cmd_test', trace_id = 'trace_test' })
      assert(protocol.validate_command(command, kind)); return command
    end,
    _request = function(_, method, path, command, callback)
      requests[#requests+1] = command
      assert(method == 'POST' and path == '/v1/attention/' .. command.attention_id)
      callback({ ok = true, attention_id = command.attention_id, dismissed = true })
    end }
  local session = { buf = notebook, controller = controller, model = { notebook_id = item.notebook_id, cell_by_id = function() return {} end },
    interactive = { surfaces = { srf_test = { client = { client_id = item.client_id }, buf = client_buf } } } }
  local manager = attention.attach(session)
  local client_win = vim.api.nvim_open_win(client_buf, true, { split = 'below' })
  vim.api.nvim_exec_autocmds('FocusLost', {})
  manager:receive(item); manager:receive(item)
  assert(attention.count(notebook, item.client_id) == 1)
  assert(tabline.summary(original_tab).action_required == 1 and vim.o.showtabline == 2)
  local rendered = vim.api.nvim_eval_statusline(vim.o.tabline, { use_tabline = true, highlights = true })
  assert(rendered.str:find('!1', 1, true))
  local has_attention = false
  for _, hl in ipairs(rendered.highlights) do
    if hl.group == 'JusiTabAttention' then has_attention = true end
  end
  assert(has_attention, 'attention tab label lacks its background highlight')
  assert(vim.wait(1000, function() return bells == 1 end, 10))
  assert(#messages == 1 and hooks == 1 and vim.api.nvim_get_current_win() == client_win)
  manager:resync({ item }); manager:receive(scenario.update)
  assert(attention.count(notebook) == 1 and #messages == 1)
  vim.api.nvim_exec_autocmds('FocusGained', {})
  assert(#requests == 0, 'visiting an approval request dismissed it')
  -- Notices in another tab notify without switching tabs or ringing a focused editor.
  vim.cmd('tabnew')
  local project_tab = vim.api.nvim_get_current_tabpage()
  -- A mirrored notebook must not claim a client visible in another tab.
  vim.api.nvim_set_current_buf(notebook)
  local notice = vim.tbl_extend('force', item, { attention_id = 'attn_notice', kind = 'notice' })
  manager:receive(notice)
  assert(vim.wait(1000, function() return #messages == 2 end, 10))
  assert(bells == 1 and vim.api.nvim_get_current_tabpage() == project_tab and attention.count(notebook) == 2)
  tabline.refresh()
  assert(tabline.summary(original_tab).count == 2 and tabline.summary(project_tab).count == 0)
  for _ = 1, 20 do vim.notify('unrelated status message') end
  assert(tabline.render():find('JusiTabAttention', 1, true), 'message spam removed the tab indicator')
  attention.show(false)
  vim.cmd.stopinsert()
  assert(vim.api.nvim_get_current_tabpage() == original_tab and vim.api.nvim_get_current_win() == client_win)
  assert(#requests == 1 and requests[1].attention_id == notice.attention_id)
  assert(attention.count(notebook) == 1, 'visit cleared an action-required item')
  manager:receive(scenario.clear)
  manager:receive(scenario.pending)
  assert(attention.count(notebook) == 0, 'stale replay resurrected a cleared request')
  assert(vim.o.tabline == '' and vim.o.showtabline == 0, 'native tabline options were not restored')
  -- Custom tablines retain ownership and can read the same per-tab summary.
  vim.o.tabline = 'custom tabs'
  local custom_notice = vim.tbl_extend('force', item, { attention_id = 'attn_custom', kind = 'notice' })
  manager:receive(custom_notice)
  assert(vim.o.tabline == 'custom tabs' and tabline.summary(original_tab).notice == 1)
  assert(tabline.render():find('JusiTabNotice', 1, true))
  vim.api.nvim_buf_set_name(client_buf, '/tmp/jusi-attention-100%test')
  assert(tabline.render():find('100%%test', 1, true), 'buffer name was interpreted as tabline syntax')
  vim.o.tabline = ''
  attention.setup({ tabline = false })
  assert(vim.o.tabline == '', 'disabled attention tabline was installed')
  attention.setup({ tabline = true })
  assert(vim.o.tabline ~= '')
  manager:retire(item.client_id)
  vim.o.tabline = ''
  local another = vim.tbl_extend('force', item, { attention_id = 'attn_retired' })
  manager:receive(another); manager:retire(item.client_id)
  assert(attention.count(notebook) == 0)
  -- A pending item first seen under another recipient still alerts when this
  -- editor becomes its owner; updates thereafter remain quiet.
  local rebound = vim.tbl_extend('force', item, { attention_id = 'attn_rebound', editor_id = 'editor_other' })
  manager:receive(rebound)
  assert(attention.count(notebook) == 0)
  rebound.editor_id, rebound.revision = item.editor_id, 2
  manager:receive(rebound)
  assert(vim.wait(1000, function() return hooks == 3 end, 10))
  -- Multiple clients offer a picker; cancellation and notice dismissal keep focus.
  local other_buf = vim.api.nvim_create_buf(false, true)
  session.interactive.surfaces.srf_other = { client = { client_id = 'cli_other' }, buf = other_buf }
  controller.clients.cli_other = { runtime_id = item.runtime_id, cell_id = 'cell_other' }
  local other = vim.tbl_extend('force', item, { attention_id = 'attn_other', client_id = 'cli_other', cell_id = 'cell_other', kind = 'notice' })
  manager:receive(other)
  local select_ui, choices, selection = vim.ui.select, nil, nil
  vim.ui.select = function(entries, opts, callback)
    choices, selection = entries, callback
    assert(opts.format_item(entries[1]):find('client', 1, true))
  end
  local before = vim.api.nvim_get_current_win()
  attention.show(false)
  assert(#choices == 2 and vim.api.nvim_get_current_win() == before)
  selection(nil)
  assert(vim.api.nvim_get_current_win() == before)
  attention.show(true)
  assert(#requests == 2 and requests[2].attention_id == other.attention_id)
  assert(attention.count(notebook) == 1 and vim.api.nvim_get_current_win() == before)
  vim.ui.select = select_ui
  -- Hidden client fallback follows notebook views; a visible client takes precedence.
  vim.api.nvim_win_close(client_win, true)
  tabline.refresh()
  assert(tabline.summary(original_tab).count == 1 and tabline.summary(project_tab).count == 1)
  local moved = vim.api.nvim_open_win(client_buf, false, { split = 'below' })
  tabline.refresh()
  assert(tabline.summary(original_tab).count == 1 and tabline.summary(project_tab).count == 0)
  vim.api.nvim_win_close(moved, true)
  manager:close()
  vim.api.nvim_set_current_tabpage(project_tab); vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(original_tab); vim.cmd('tabclose!')
  vim.api.nvim_buf_delete(client_buf, { force = true })
  vim.api.nvim_buf_delete(other_buf, { force = true })
  vim.api.nvim_buf_delete(notebook, { force = true })
  attention.setup({ bell = true, notify = false })
  vim.o.tabline, vim.o.showtabline = original_tabline, original_showtabline
  vim.notify = notify
end
return M
