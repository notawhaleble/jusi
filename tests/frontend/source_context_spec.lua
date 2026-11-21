local M = {}
function M.run()
  local contexts = require('jusi.source_context')
  local owner = {}
  local host, defer = package.loaded.jusi_neovim, vim.defer_fn
  local reader = vim.api.nvim_create_buf(false, true)
  local plain = vim.api.nvim_create_buf(false, true)
  vim.b[reader].jusi_provider_id = 'reader'
  local expire, deliver, cancellations = nil, nil, 0
  vim.defer_fn = function(callback, timeout)
    assert(timeout == 120000)
    expire = callback
  end
  package.loaded.jusi_neovim = { snapshot = function(_, callback)
    deliver = callback
    return function() cancellations = cancellations + 1 end
  end }
  local ok, failure = xpcall(function()
    contexts.capture(owner, 'cell', reader)
    contexts.capture(owner, 'cell', plain)
    local command = { trace_id = 'trace_new' }
    contexts.submit(owner, command, 'cell')
    assert(not command.source_context and cancellations == 1)
    contexts.capture(owner, 'cell', reader)
    contexts.close(owner, 'cell', 'old-client')
    contexts.submit(owner, command, 'cell')
    assert(command.source_context)
    contexts.bind_client(owner, { client_id = 'new', cell_id = 'cell' }, command.trace_id)
    contexts.request(owner, 'cell', command.source_context, function(err) assert(err) end, 'old')
    local replies, releases = 0, 0
    contexts.request(owner, 'cell', command.source_context, function(err, snapshot)
      assert(err and not snapshot)
      replies = replies + 1
    end, 'new')
    expire()
    deliver(nil, { release = function() releases = releases + 1 end })
    expire()
    assert(replies == 1 and releases == 1 and cancellations == 2)
    assert(next(owner.source_contexts) == nil)
  end, debug.traceback)
  package.loaded.jusi_neovim, vim.defer_fn = host, defer
  vim.api.nvim_buf_delete(reader, { force = true })
  vim.api.nvim_buf_delete(plain, { force = true })
  assert(ok, failure)
end
return M
