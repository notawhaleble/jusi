local M = {}
function M.run()
  local jusi = require('jusi'); jusi.setup()
  local documents = require('jusi.presentation.documents')
  local initial, host = vim.api.nvim_get_current_tabpage(), package.loaded.jusi_neovim
  local buffers, stages = {}, {}
  local function buffer()
    local buf = vim.api.nvim_create_buf(false, true)
    vim.bo[buf].bufhidden = 'hide'
    buffers[#buffers+1] = buf
    return buf
  end
  package.loaded.jusi_neovim = {
    provider_for = function() return 'markdown' end,
    find_document = function() end,
    open = function(_, path, opts)
      local buf = buffer()
      stages[buf] = vim.fn.fnamemodify(path, ':h')
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.fn.readfile(path))
      vim.api.nvim_set_current_buf(buf)
      opts.on_ready(nil, buf)
      return buf
    end,
  }
  vim.cmd.tabnew()
  local source_tab, notebook_buf = vim.api.nvim_get_current_tabpage(), buffer()
  vim.api.nvim_set_current_buf(notebook_buf)
  vim.api.nvim_buf_set_lines(notebook_buf, 0, -1, false, { '╭──', 'explain', '╰──' })
  local model = require('jusi.notebook').attach(notebook_buf)
  local cell = model:ordered_cells()[1]
  local presentation = require('jusi.presentation').new({ notebook_buf = notebook_buf, notebook_id = model.notebook_id })
  local interactive = setmetatable({ surfaces = {} }, require('jusi.presentation.interactive_terminal').InteractiveTerminals)
  local pending, failures = {}, {}
  local controller = { clients = {}, executions = {},
    close_client = function(_, id, done) assert(not pending[id]); pending[id] = done end }
  local lifecycle = require('jusi.cell_lifecycle').new({ model = model, presentation = presentation,
    controller = controller, on_failure = function(err) failures[#failures+1] = err end })
  local session = { buf = notebook_buf, model = model, controller = controller, lifecycle = lifecycle,
    presentation = presentation, interactive = interactive, url_slots = {} }
  jusi._sessions[notebook_buf] = session
  vim.cmd.tabnew()
  local project_tab, project_win = vim.api.nvim_get_current_tabpage(), vim.api.nvim_get_current_win()
  local book = buffer(); vim.api.nvim_set_current_buf(book)
  local counter = 0
  local function client()
    counter = counter + 1
    local id, terminal = 'document-client-' .. counter, buffer()
    controller.clients[id] = { client_id = id, cell_id = cell.id }
    interactive.surfaces[id] = { buf = terminal, client = controller.clients[id] }
    session.url_slots[id] = { book = { buf = book, lifetime = 'independent', close = function() error('closed book') end } }
    local win = vim.api.nvim_open_win(terminal, false, { split = 'below', win = project_win })
    local path = vim.fn.tempname(); vim.fn.writefile({ '# Explanation' }, path)
    local wire = require('jusi.artifacts').encode({ metadata = { document_id = id },
      files = { { path = path, name = 'explanation.md', size = vim.fn.getfsize(path), media_type = 'text/markdown' } } })
    local action = documents.apply({ action = 'open_document', slot = 'explanation', lifetime = 'client' },
      { win = win, source_buf = terminal, source_path = wire, url_slots = session.url_slots[id] })
    local ready = false
    action.start(function(ok) assert(ok); ready = true end)
    assert(ready)
    os.remove(path); os.remove(wire)
    return id, terminal, session.url_slots[id].explanation.buf, win
  end
  local function resolve(id, failure)
    local done = assert(pending[id]); pending[id] = nil
    if not failure then
      -- Match production client.closed ordering: terminal teardown precedes
      -- on_client_closed and its owned-document cleanup.
      interactive:close_surface(id)
      controller.clients[id] = nil
      documents.close_owned(session.url_slots[id])
      session.url_slots[id] = nil
    end
    done(failure and nil or {}, failure)
  end
  local mirror = vim.api.nvim_open_win(notebook_buf, false, { split = 'left', win = project_win })
  local id, terminal, page, win = client()
  assert(#vim.fn.win_findbuf(terminal) == 0)
  vim.api.nvim_feedkeys('\\Q', 'xt', false)
  assert(pending[id] and vim.api.nvim_win_is_valid(mirror))
  -- Repeated invocation coalesces, and completion after a tab switch is safe.
  jusi.close_tab()
  vim.api.nvim_set_current_tabpage(source_tab)
  resolve(id)
  assert(vim.api.nvim_get_current_tabpage() == source_tab)
  assert(not vim.api.nvim_win_is_valid(win) and not vim.api.nvim_win_is_valid(mirror))
  assert(not vim.api.nvim_buf_is_valid(page) and not vim.api.nvim_buf_is_valid(terminal))
  assert(vim.fn.isdirectory(stages[page]) == 0, 'document staging leaked')
  assert(#vim.api.nvim_tabpage_list_wins(project_tab) == 1 and vim.api.nvim_win_get_buf(project_win) == book)
  assert(#vim.fn.win_findbuf(notebook_buf) == 1)

  vim.api.nvim_set_current_tabpage(project_tab)
  -- No notebook mirror: discover the session through the explanation alone.
  -- A terminal in another tab protects the same client.
  id, terminal, page, win = client()
  local shared = vim.api.nvim_open_win(terminal, false, { split = 'below', win = vim.api.nvim_tabpage_get_win(source_tab) })
  jusi.close_tab()
  assert(not pending[id] and controller.clients[id] and not vim.api.nvim_win_is_valid(win))
  assert(vim.api.nvim_buf_is_valid(page) and vim.api.nvim_win_is_valid(shared))
  vim.api.nvim_win_close(shared, true)
  win = vim.api.nvim_open_win(page, false, { split = 'below', win = project_win })
  shared = vim.api.nvim_open_win(page, false, { split = 'below', win = vim.api.nvim_tabpage_get_win(source_tab) })
  jusi.close_tab()
  assert(not pending[id] and not vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_is_valid(shared))
  vim.api.nvim_win_close(shared, true)
  -- An independent book cannot select its hidden client for cleanup.
  local notify = vim.notify; vim.notify = function() end
  jusi.close_tab()
  vim.notify = notify
  assert(not pending[id] and controller.clients[id])
  win = vim.api.nvim_open_win(page, false, { split = 'below', win = project_win })
  jusi.close_tab(); resolve(id, { reason = 'unreachable' })
  assert(#failures == 1 and vim.api.nvim_win_is_valid(win) and controller.clients[id])
  jusi.close_tab()
  -- A user repurposes the old overlay split while backend close is pending.
  vim.api.nvim_win_set_buf(win, book)
  resolve(id)
  assert(vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == book)
  vim.api.nvim_win_close(win, true)

  -- Closing only the document still reveals the healthy terminal.
  id, terminal, page, win = client()
  documents.apply({ action = 'close_document', slot = 'explanation' }, { url_slots = session.url_slots[id] })
  assert(vim.api.nvim_win_get_buf(win) == terminal and controller.clients[id])
  jusi.close_tab(); resolve(id)
  assert(not vim.api.nvim_win_is_valid(win) and vim.api.nvim_buf_is_valid(book))

  lifecycle:detach(); presentation:close(); model:detach()
  jusi._sessions[notebook_buf] = nil
  package.loaded.jusi_neovim = host
  vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(source_tab); vim.cmd('tabclose!')
  vim.api.nvim_set_current_tabpage(initial)
  for _, buf in ipairs(buffers) do
    if vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_delete(buf, { force = true }) end
  end
end
return M
