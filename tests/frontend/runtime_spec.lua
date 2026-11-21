local jusi = require("jusi")

local M = {}

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
  end
end

local function truthy(value, message)
  if not value then
    error(message or "expected a truthy value")
  end
end

local FakeTransport = {}
FakeTransport.__index = FakeTransport

function FakeTransport.new()
  return setmetatable({ transport_id = "trn_runtime", requests = {} }, FakeTransport)
end

function FakeTransport:connect_events(_, callbacks)
  self.callbacks = callbacks
  local connection = {
    closed = false,
    close = function(value)
      value.closed = true
    end,
  }
  callbacks.on_open()
  return connection
end

function FakeTransport:request(method, path, payload, _, callback)
  table.insert(self.requests, { method = method, path = path, payload = payload })
  if path == "/v1/health" then
    callback({
      ok = true,
      status = "ready",
      executions = {},
      clients = {},
      surfaces = {},
      supervisor_id = "sup_runtime",
      earliest_event_sequence = 1,
      event_sequence = 0,
      kernel = nil,
      runtime = vim.NIL,
    }, nil)
  elseif payload.kind == "start_kernel" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime", state = "on" },
      runtime = {
        runtime_id = "run_runtime",
        discovery_id = "discovery_runtime",
        plugin_catalog = { protocol_version = 1, catalog_version = 1, discovery_id = "discovery_runtime", plugins = {} },
      },
    }, nil)
  elseif payload.kind == "execute" then
    callback({ ok = true, execution = { execution_id = "exe_runtime", outcome = "succeeded" } }, nil)
  elseif payload.kind == "interrupt" then
    callback({
      ok = true,
      execution = { execution_id = payload.execution_id, outcome = "running" },
      interrupt = { result = "requested" },
    }, nil)
  elseif payload.kind == "restart_notebook" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime_2", state = "on" },
      runtime = {
        runtime_id = "run_runtime_2",
        notebook_id = payload.next_notebook_id,
        discovery_id = "discovery_runtime_2",
        kernel_id = "krn_runtime_2",
        plugin_catalog = { protocol_version = 1, catalog_version = 1, discovery_id = "discovery_runtime_2", plugins = {} },
      },
      cleanup = { result = "stopped" },
    }, nil)
  elseif payload.kind == "stop_kernel" then
    callback({
      ok = true,
      kernel = { kernel_id = "krn_runtime", state = "off" },
      cleanup = { result = "stopped" },
    }, nil)
  elseif payload.kind == "close_client" then
    callback({
      ok = true,
      client_id = payload.client_id,
      cleanup = { result = "stopped" },
    }, nil)
  end
  return {}
end

local function test_client_commands_use_focused_projection_identity()
  local original_notify = vim.notify
  vim.notify = function() end
  local ok, failure = xpcall(function()
    local notebook_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { "╭──", "%%sql main", "select 1", "╰──" })
    vim.api.nvim_win_set_buf(0, notebook_buf)
    local transport = FakeTransport.new()
    local session = jusi.connect({ buf = notebook_buf, transport = transport })
    local cell_id = session.model:cell_at_row(1).id
    local client_buf = vim.api.nvim_create_buf(false, true)
    local terminal_lines = {}
    for _ = 1, 20 do
      table.insert(terminal_lines, "terminal row")
    end
    vim.api.nvim_buf_set_lines(client_buf, 0, -1, false, terminal_lines)
    vim.b[client_buf].jusi_role = "interactive_terminal"
    vim.b[client_buf].jusi_notebook_id = session.model.notebook_id
    vim.b[client_buf].jusi_cell_id = cell_id
    vim.b[client_buf].jusi_client_id = "cli_sql"
    vim.api.nvim_win_set_buf(0, client_buf)
    vim.api.nvim_win_set_cursor(0, { 12, 0 })

    session.controller.kernel_id = "krn_runtime"
    session.controller.kernel_state = "on"
    jusi.execute()
    equal(transport.requests[2].method, "POST")
    equal(transport.requests[2].path, "/v1/kernels/krn_runtime/executions")
    equal(transport.requests[2].payload.cell_id, cell_id)
    equal(transport.requests[2].payload.code, "%%sql main\nselect 1")

    session.controller.executions.exe_active = {
      execution_id = "exe_active",
      cell_id = cell_id,
      outcome = "running",
    }
    jusi.interrupt()
    equal(transport.requests[3].path, "/v1/kernels/krn_runtime/executions/exe_active/interrupt")
    equal(transport.requests[3].payload.execution_id, "exe_active")
    session.controller.executions.exe_active.outcome = "interrupted"

    session.controller.clients.cli_sql = {
      client_id = "cli_sql",
      cell_id = cell_id,
    }
    jusi.close()

    equal(transport.requests[4].method, "DELETE")
    equal(transport.requests[4].path, "/v1/clients/cli_sql")
    equal(transport.requests[4].payload.client_id, "cli_sql")

    session.controller.clients.cli_sql = nil
    session.controller.clients.cli_replaced = {
      client_id = "cli_replaced",
      cell_id = cell_id,
    }
    jusi.execute()
    equal(transport.requests[5].path, "/v1/clients/cli_replaced")
    equal(transport.requests[5].payload.kind, "close_client")
    equal(transport.requests[6].path, "/v1/kernels/krn_runtime/executions")
    equal(transport.requests[6].payload.cell_id, cell_id)
    vim.api.nvim_buf_delete(client_buf, { force = true })
    equal(jusi._destroy_session(notebook_buf), true)
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

local function event(sequence, kind, payload)
  local resource_kind = kind == "service.ready" and "supervisor" or "execution"
  local resource_id = kind == "service.ready" and "sup_runtime" or payload.execution_id
  return {
    protocol_version = 1,
    event_id = "evt_" .. sequence,
    supervisor_id = "sup_runtime",
    sequence = sequence,
    occurred_at = "2026-08-31T12:00:00Z",
    trace_id = "trace_runtime",
    layer = kind == "service.ready" and "service" or "execution",
    operation = kind == "service.ready" and "service_start" or "execute",
    kind = kind,
    resource = { kind = resource_kind, id = resource_id },
    payload = payload,
  }
end

local function test_explicit_command_workflow()
  local notifications = {}
  local original_notify = vim.notify
  vim.notify = function(message, level)
    table.insert(notifications, { message = message, level = level })
  end
  local ok, failure = xpcall(function()
    jusi.setup({ output_height = 5 })
    equal(vim.fn.exists(":JusiConnect"), 2)
    equal(vim.fn.exists(":JusiExecute"), 2)
    equal(vim.fn.exists(":JusiInterrupt"), 2)
    equal(vim.fn.exists(":JusiServiceStart"), 2)
    equal(vim.fn.exists(":JusiServiceStop"), 2)
    equal(vim.fn.exists(":JusiRestart"), 2)
    equal(vim.fn.exists(":JusiClose"), 2)
    equal(vim.fn.exists(":JusiToggleFocus"), 2)

    local notebook_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { "╭──", "1 + 1", "╰──" })
    vim.api.nvim_win_set_buf(0, notebook_buf)
    local notebook_window = vim.api.nvim_get_current_win()
    local transport = FakeTransport.new()
    local session = jusi.connect({ buf = notebook_buf, transport = transport })
    transport.callbacks.on_event(event(1, "service.ready", { supervisor_id = "sup_runtime" }))

    jusi.start_kernel(notebook_buf)
    equal(session.controller.kernel_state, "on")
    jusi.execute(notebook_buf, 1)
    equal(transport.requests[3].payload.code, "1 + 1")
    local cell_id = session.model:cell_at_row(1).id
    transport.callbacks.on_event(event(2, "execution.started", {
      execution_id = "exe_runtime",
      kernel_id = "krn_runtime",
      notebook_id = session.model.notebook_id,
      cell_id = cell_id,
      client_id = "cli_runtime",
      outcome = "running",
      started_at = "2026-08-31T12:00:00Z",
      completed_at = vim.NIL,
    }))
    transport.callbacks.on_event(event(3, "execution.output", {
      execution_id = "exe_runtime",
      client_id = "cli_runtime",
      output_kind = "result",
      media_type = "text/plain",
      data = "2",
    }))
    local output_buf = assert(session.presentation:buffer_for_cell(cell_id))
    equal(vim.api.nvim_get_current_buf(), notebook_buf, "automatic presentation must not steal focus")
    equal(#vim.fn.win_findbuf(output_buf), 1, "execution must reveal its artifact")
    assert(vim.wait(1000, function()
      return vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1] == "2"
    end, 10), "terminal output was not rendered")
    equal(vim.api.nvim_buf_get_lines(output_buf, 0, 1, false)[1], "2")

    equal(jusi.toggle_focus(notebook_buf, 1), output_buf)
    equal(vim.api.nvim_get_current_buf(), output_buf)
    equal(jusi.toggle_focus(), notebook_buf)
    equal(vim.api.nvim_get_current_win(), notebook_window)
    equal(vim.api.nvim_get_current_buf(), notebook_buf)

    jusi.toggle_focus(notebook_buf, 1)
    vim.api.nvim_win_close(0, false)
    equal(#vim.fn.win_findbuf(output_buf), 0, "native window close must only hide the artifact")
    equal(vim.api.nvim_buf_is_valid(output_buf), true)
    equal(jusi.toggle_focus(notebook_buf, 1), output_buf)
    equal(vim.api.nvim_get_current_buf(), output_buf, "toggle must reopen a hidden artifact")
    equal(jusi.toggle_focus(), notebook_buf)
    equal(jusi.close(notebook_buf, 1), true)
    equal(vim.api.nvim_buf_is_valid(output_buf), false, "JusiClose must destroy ordinary output")

    local old_notebook_id = session.model.notebook_id
    local old_cell_id = cell_id
    local text_before_restart = vim.api.nvim_buf_get_lines(notebook_buf, 0, -1, false)
    jusi.setup({ kernel_name = "python-reloaded" })
    jusi.restart(notebook_buf)
    equal(transport.requests[4].payload.kind, "interrupt", "close must interrupt an active execution")
    equal(transport.requests[5].payload.kernel_name, "python-reloaded")
    equal(session.buf, notebook_buf)
    equal(vim.api.nvim_buf_get_lines(notebook_buf, 0, -1, false), text_before_restart)
    truthy(session.model.notebook_id ~= old_notebook_id, "restart must replace frontend notebook identity")
    truthy(session.model:ordered_cells()[1].id ~= old_cell_id, "restart must replace runtime cell identity")
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(session.controller.runtime_id, "run_runtime_2")
    equal(session.controller.kernel_id, "krn_runtime_2")
    equal(session.controller.notebook, session.model)
    jusi.setup({ kernel_name = "python3" })

    jusi.stop_kernel()
    equal(session.controller.kernel_state, "off")
    equal(jusi.disconnect(), true)
    equal(session.controller.transport_state, "disconnected")
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(jusi._sessions[notebook_buf], session)
    local notebook_id = session.model.notebook_id
    jusi.connect({ buf = notebook_buf })
    equal(session.model.notebook_id, notebook_id, "transport reconnection must preserve model identity")
    equal(jusi._destroy_session(notebook_buf), true)
    equal(vim.api.nvim_buf_is_valid(output_buf), false)
    equal(jusi._sessions[notebook_buf], nil)
    assert(#notifications >= 3)
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

local function test_vipynb_filetype_and_legacy_connection_guard()
  vim.cmd("runtime ftdetect/jusi.lua")
  equal(vim.filetype.match({ filename = "notebook.vipynb" }), "jusi")

  local notifications = {}
  local original_notify = vim.notify
  vim.notify = function(message, level)
    table.insert(notifications, { message = message, level = level })
  end
  local ok, failure = xpcall(function()
    local legacy_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(legacy_buf, 0, -1, false, { "##", "1 + 1" })
    equal(jusi.connect({ buf = legacy_buf, transport = FakeTransport.new() }), nil)
    equal(jusi._sessions[legacy_buf], nil)
    assert(notifications[1].message:find("legacy 0.x", 1, true))
    vim.api.nvim_buf_delete(legacy_buf, { force = true })
  end, debug.traceback)
  vim.notify = original_notify
  if not ok then
    error(failure)
  end
end

local function test_editor_open_anchors_to_requesting_client_in_project_tab()
  local original = vim.api.nvim_get_current_win()
  vim.cmd('tabnew')
  local notebook_win = vim.api.nvim_get_current_win()
  local notebook_buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { '╭──', '1', '╰──' })
  local session = jusi.connect({ buf = notebook_buf, transport = FakeTransport.new() })
  session.controller.on_failure = require('jusi.diagnostics').record
  local client_buf = vim.api.nvim_create_buf(false, true)
  local client = { client_id = 'cli_open_position', runtime_id = 'run_open_position', cell_id = session.model:cell_at_row(1).id }
  session.controller.clients[client.client_id] = client
  session.interactive.surfaces.srf_open_position = { buf = client_buf, client = client }
  local first_client = vim.api.nvim_open_win(client_buf, false, { split = 'below', win = notebook_win })
  local first_tab = vim.api.nvim_get_current_tabpage()
  local first_layout = vim.fn.winlayout()

  vim.cmd('tabnew')
  local project_tab = vim.api.nvim_get_current_tabpage()
  local mirror = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(mirror, notebook_buf)
  local client_win = vim.api.nvim_open_win(client_buf, true, { split = 'right', win = mirror })
  local pending, outcome, sequence = nil, nil, 0
  session.controller.transport.request = function(_, method, _, payload, _, callback)
    if method == 'GET' then pending = callback
    else
      outcome = payload.outcome
      callback({ ok = true, delivery = { action_id = payload.action_id, outcome = outcome, reason = '' } })
    end
  end
  local function begin_snapshot()
    sequence = sequence + 1
    pending, outcome = nil, nil
    local action = { action = 'open', action_id = 'act_position_' .. sequence, client_id = client.client_id,
      runtime_id = client.runtime_id, cell_id = client.cell_id, notebook_id = session.model.notebook_id,
      editor_id = session.controller.editor_id, trace_id = 'trace_open_position' }
    session.controller.on_editor_action(action)
    return { ok = true, action = action, remaining_ms = 29000,
      content = { action = 'open', text = 'selected value', name = 'value.txt', filetype = 'text' } }
  end
  local function open_snapshot(during_transfer)
    local response = begin_snapshot()
    assert(pending, 'visible client did not start delivery')
    if during_transfer then
      -- Exercise the chunked remote path and change focus before the last chunk.
      response.offset, response.next_offset, response.eof = 0, #response.content.text, false
      pending(vim.deepcopy(response))
      during_transfer()
      response.offset, response.eof = response.next_offset, true
      response.content.text = ''
    end
    pending(response)
    equal(outcome, 'delivered')
    return vim.api.nvim_get_current_buf()
  end
  local function check_open(anchor, during_transfer)
    local position = vim.api.nvim_win_get_position(anchor)
    local width = vim.api.nvim_win_get_width(anchor)
    local exported = assert(open_snapshot(during_transfer))
    local opened = vim.api.nvim_get_current_win()
    equal(vim.api.nvim_get_current_tabpage(), project_tab, 'open jumped to the original notebook tab')
    equal(vim.api.nvim_win_get_position(opened)[2], position[2], 'open split was not below the requesting client')
    equal(vim.api.nvim_win_get_width(opened), width)
    equal(vim.api.nvim_buf_get_lines(exported, 0, -1, false), { 'selected value' })
    vim.api.nvim_win_close(opened, true)
    vim.api.nvim_buf_delete(exported, { force = true })
  end
  local switchbuf = vim.o.switchbuf
  vim.o.switchbuf = 'useopen,usetab'
  check_open(client_win, function() vim.api.nvim_set_current_win(first_client) end)
  -- Notebook placement, including being entirely hidden, is irrelevant.
  local unrelated = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_win_set_buf(notebook_win, unrelated)
  vim.api.nvim_win_set_buf(mirror, unrelated)
  vim.api.nvim_set_current_win(client_win)
  check_open(client_win)
  vim.api.nvim_win_set_buf(notebook_win, notebook_buf)
  vim.api.nvim_win_set_buf(mirror, notebook_buf)
  -- If focus has moved back to the mirrored notebook, keep the client anchor
  -- in this tab rather than choosing its earlier view in the first tab.
  vim.api.nvim_set_current_win(mirror)
  check_open(client_win)
  -- Two views of the client in one tab: the active view wins.
  local other_view = vim.api.nvim_open_win(client_buf, true, { split = 'right', win = mirror })
  check_open(other_view)
  equal(vim.api.nvim_win_call(notebook_win, function() return vim.fn.winlayout() end), first_layout,
    'opening snapshots changed the original notebook tab')
  -- Reusing the captured window must not redirect to the other client view,
  -- the notebook, or an unrelated replacement buffer.
  vim.api.nvim_set_current_win(other_view)
  local response = begin_snapshot()
  vim.api.nvim_win_set_buf(other_view, unrelated)
  pending(response)
  equal(outcome, 'failed')
  equal(vim.api.nvim_get_current_win(), other_view)
  vim.api.nvim_win_set_buf(other_view, client_buf)
  response = begin_snapshot()
  vim.api.nvim_win_close(other_view, true)
  local before = vim.api.nvim_get_current_win()
  pending(response)
  equal(outcome, 'failed')
  equal(vim.api.nvim_get_current_win(), before)
  vim.api.nvim_win_close(client_win, true)
  vim.api.nvim_win_close(first_client, true)
  vim.api.nvim_set_current_win(mirror)
  begin_snapshot()
  equal(outcome, 'failed', 'hidden client fell back to a notebook window')
  equal(pending, nil, 'hidden client unnecessarily fetched content')
  vim.o.switchbuf = switchbuf
  vim.api.nvim_set_current_tabpage(first_tab)
  equal(vim.fn.winlayout(), { 'leaf', notebook_win })
  vim.api.nvim_set_current_tabpage(project_tab)
  jusi._destroy_session(notebook_buf)
  vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(first_tab)
  vim.cmd('tabclose!')
  vim.api.nvim_buf_delete(notebook_buf, { force = true })
  vim.api.nvim_buf_delete(unrelated, { force = true })
  vim.api.nvim_set_current_win(original)
end

local function test_document_actions_use_session_destination_and_trace_failures()
  local original_notify = vim.notify
  vim.notify = function() end
  local original = vim.api.nvim_get_current_win()
  vim.cmd('tabnew')
  local notebook_buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { '╭──', '%%read road', '╰──' })
  local root = vim.fn.tempname(); vim.fn.mkdir(root, '', 448)
  local shared = { directory = root, token = string.rep('a', 64) }
  local session = jusi.connect({ buf = notebook_buf, transport = FakeTransport.new(), service = { shared_artifacts = shared } })
  local client = { client_id = 'cli_document', runtime_id = 'run_document', cell_id = session.model:cell_at_row(1).id }
  session.controller.clients[client.client_id] = client
  local source = vim.api.nvim_create_buf(false, true)
  session.interactive.surfaces.srf_document = { buf = source, client = client }
  local source_win = vim.api.nvim_open_win(source, false, { split = 'right', win = 0 })
  local host_before = package.loaded.jusi_neovim
  local opened, reject = {}, false
  package.loaded.jusi_neovim = {
    provider_for = function(mime) return mime == 'application/pdf' and 'pdf' or nil end,
    find_document = function(identity)
      for _, buf in ipairs(opened) do
        if vim.api.nvim_buf_is_valid(buf) and vim.b[buf].identity == identity then return buf end
      end
    end,
    open = function(_, path, opts)
      assert(vim.api.nvim_get_current_win() ~= original, 'Document escaped its source tab')
      assert(vim.fn.filereadable(path) == 1)
      local buf = vim.api.nvim_create_buf(false, true)
      vim.b[buf].jusi_provider_id, vim.b[buf].identity = 'pdf', opts.document_id
      vim.api.nvim_set_current_buf(buf); table.insert(opened, buf)
      vim.schedule(function() opts.on_ready(reject and 'Fixture reader rejected document' or nil, buf) end)
      return buf
    end,
  }
  local pending, outcome, reply, seq
  seq = 0
  session.controller.transport.request = function(_, method, path, payload, _, callback)
    if method == 'GET' then pending = callback
    elseif path:match('/reply$') then
      reply = (reply or '') .. payload.text
      callback({ next_offset = payload.offset + #payload.text, eof = payload.eof })
    else
      outcome = payload.outcome
      callback({ ok = true, delivery = { action_id = payload.action_id, outcome = outcome, reason = '' } })
    end
  end
  local function deliver(kind, text, extra)
    seq = seq + 1; pending, outcome, reply = nil, nil, nil
    local action = { action = kind, action_id = 'act_document_' .. seq, client_id = client.client_id,
      runtime_id = client.runtime_id, cell_id = client.cell_id, notebook_id = session.model.notebook_id,
      editor_id = session.controller.editor_id, trace_id = 'trace_document_' .. seq }
    session.controller.on_editor_action(action)
    assert(pending, 'Document did not capture its source')
    pending({ ok = true, action = action, remaining_ms = 29000, offset = 0, next_offset = #text, eof = true,
      content = vim.tbl_extend('force', { action = kind, text = text, slot = 'book', provider = '', lifetime = 'independent' }, extra or {}) })
    assert(vim.wait(1000, function() return outcome ~= nil end, 10))
    return action
  end
  local function header(identity)
    return { version = 1, metadata = { document_id = identity },
      files = { { name = 'document.pdf', media_type = 'application/pdf', size = 4 } } }
  end
  local function stage(id)
    local directory = root .. '/' .. vim.fn.sha256(client.client_id) .. '/' .. id
    vim.fn.mkdir(directory, 'p', 448)
    local file = assert(io.open(directory .. '/document.pdf', 'wb')); file:write('book'); file:close()
  end
  local ok, failure = xpcall(function()
    for index, hidden in ipairs({ false, true }) do
      if hidden then
        for _, win in ipairs(vim.fn.win_findbuf(source)) do vim.api.nvim_win_close(win, true) end
      end
      local manifest = vim.json.encode(header('shared-book-' .. index))
      deliver('offer_document', manifest, { shared_token = shared.token })
      equal(outcome, 'delivered'); equal(vim.json.decode(reply).transport, 'shared')
      local id = string.rep(tostring(index), 32); stage(id)
      deliver('open_shared_document', manifest, { shared_token = shared.token, artifact_id = id })
      equal(outcome, 'delivered', 'Shared document received no source window')
      local count = #opened
      deliver('offer_document', manifest, { shared_token = shared.token })
      equal(vim.json.decode(reply).transport, 'reused'); equal(#opened, count)
    end
    -- HTTP fallback uses the same production session placement.
    deliver('offer_document', vim.json.encode(header('http-book')), { shared_token = '' })
    equal(vim.json.decode(reply).transport, 'http')
    local page = vim.fn.tempname(); vim.fn.writefile({ 'book' }, page, 'b')
    local wire = require('jusi.artifacts').encode({ metadata = { document_id = 'http-book' },
      files = { { name = 'document.pdf', path = page, media_type = 'application/pdf', size = 4 } } })
    local f = assert(io.open(wire, 'rb')); local text = f:read('*a'); f:close(); os.remove(wire); os.remove(page)
    deliver('open_document', text); equal(outcome, 'delivered')
    local owned_id = string.rep('c', 32)
    stage(owned_id)
    local owned_manifest = vim.json.encode(header('client-book'))
    deliver('open_shared_document', owned_manifest, { shared_token = shared.token, artifact_id = owned_id, lifetime = 'client' })
    equal(outcome, 'delivered')
    local owner = client
    local owned = opened[#opened]
    client = { client_id = 'cli_other_document', runtime_id = owner.runtime_id, cell_id = owner.cell_id }
    session.controller.clients[client.client_id] = client
    local other_source = vim.api.nvim_create_buf(false, true)
    session.interactive.surfaces.srf_other_document = { buf = other_source, client = client }
    deliver('offer_document', owned_manifest, { shared_token = shared.token })
    equal(vim.json.decode(reply).transport, 'shared', 'Independent offer reused a client-owned buffer')
    stage(owned_id)
    deliver('open_shared_document', owned_manifest, { shared_token = shared.token, artifact_id = owned_id })
    equal(outcome, 'delivered')
    local independent = opened[#opened]
    assert(independent ~= owned)
    session.controller.on_client_closed(owner, owner)
    assert(not vim.api.nvim_buf_is_valid(owned) and vim.api.nvim_buf_is_valid(independent))
    -- A provider readiness failure must use the same trace as its action.
    reject = true
    local id = string.rep('f', 32); stage(id)
    local action = deliver('open_shared_document', vim.json.encode(header('rejected-book')), { shared_token = shared.token, artifact_id = id })
    equal(outcome, 'failed')
    local failures = require('jusi.diagnostics').find(action.trace_id)
    equal(#failures, 1); equal(failures[1].layer, 'frontend_presentation')
    equal(failures[1].details.action_id, action.action_id)
    assert(failures[1].message:find('Fixture reader rejected document', 1, true))
    equal(session.controller.kernel_state, 'off') -- presentation never changes kernel truth
    assert(session.controller.clients[client.client_id] == client)
  end, debug.traceback)
  for _, buf in ipairs(opened) do if vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_delete(buf, { force = true }) end end
  package.loaded.jusi_neovim = host_before
  -- This test supplies a shared filesystem descriptor, not an owned process.
  session.service = nil
  jusi._destroy_session(notebook_buf)
  vim.fn.delete(root, 'rf')
  vim.cmd('tabclose!')
  vim.api.nvim_set_current_win(original)
  vim.notify = original_notify
  assert(ok, failure)
end

local function test_mirrored_notebook_commands()
  vim.cmd.tabnew()
  local buf, left = vim.api.nvim_get_current_buf(), vim.api.nvim_get_current_win()
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { '╭──', 'first()', '╰──', '╭──', 'second()', '╰──' })
  local right = vim.api.nvim_open_win(buf, true, { split = 'right', win = left })
  local transport = FakeTransport.new()
  local session = jusi.connect({ buf = buf, transport = transport })
  jusi.start_kernel(buf)
  session.editing.cellmode:set(true)
  vim.api.nvim_win_set_cursor(left, { 2, 1 })
  vim.api.nvim_win_set_cursor(right, { 5, 2 })
  for _, win in ipairs({ left, right, left }) do
    vim.api.nvim_set_current_win(win)
    vim.cmd('JusiExecute')
    local expected = win == left and 'first()' or 'second()'
    equal(transport.requests[#transport.requests].payload.code, expected, 'Execution used another pane')
  end
  local cells = session.model:ordered_cells()
  for index, cell in ipairs(cells) do
    local id = 'cli_pane_' .. index
    session.controller.clients[id] = { client_id = id, cell_id = cell.id, notebook_id = session.model.notebook_id }
  end
  vim.api.nvim_set_current_win(right)
  vim.api.nvim_feedkeys('Q', 'xt', false)
  equal(transport.requests[#transport.requests].payload.client_id, 'cli_pane_2', 'Q selected another pane')
  jusi._destroy_session(buf)
  vim.cmd('tabclose!')
  vim.api.nvim_buf_delete(buf, { force = true })
end

function M.run()
  test_mirrored_notebook_commands()
  test_document_actions_use_session_destination_and_trace_failures()
  test_explicit_command_workflow()
  test_client_commands_use_focused_projection_identity()
  test_vipynb_filetype_and_legacy_connection_guard()
  test_editor_open_anchors_to_requesting_client_in_project_tab()
end

return M
