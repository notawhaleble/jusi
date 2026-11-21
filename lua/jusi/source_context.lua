-- Each editing/runtime owner retains its own captures. Context travels by an
-- opaque submission identity, never by prompt text or a provider-specific anchor.
local M = {}
local function release(record)
  if record.closed then return end
  record.closed = true
  if record.cancel then record.cancel() end
  if record.snapshot then record.snapshot.release(); record.snapshot = nil end
  if record.waiter then record.waiter("Source context expired or was closed") end
end
function M.capture(owner, cell_id, buf)
  owner.source_contexts = owner.source_contexts or {}
  local records = owner.source_contexts
  for id, record in pairs(records) do
    if record.cell_id == cell_id and not record.submitted then release(record); records[id] = nil end
  end
  if not buf then return end
  local ok, host = pcall(require, 'jusi_neovim')
  if not ok or not host.snapshot or not vim.b[buf].jusi_provider_id then return end
  assert(vim.tbl_count(records) < 32, 'Too many pending source captures')
  local id = 'ctx_' .. vim.fn.sha256(tostring(vim.uv.hrtime()) .. ':' .. buf .. ':' .. cell_id):sub(1, 32)
  local record = { id = id, cell_id = cell_id }
  records[id] = record
  record.cancel = host.snapshot(buf, function(err, snapshot)
    if record.closed then if snapshot then snapshot.release() end; return end
    if snapshot then snapshot.source_buf = buf end
    record.error, record.snapshot, record.ready = err, snapshot, true
    if record.waiter then record.waiter(err, snapshot) end
  end)
end
function M.submit(owner, command, cell_id)
  for id, record in pairs(owner.source_contexts or {}) do
    if record.cell_id == cell_id and not record.submitted then
      record.submitted = true
      record.trace_id = command.trace_id
      record.client_id = command.client_id
      command.source_context = id
      vim.defer_fn(function()
        if owner.source_contexts and owner.source_contexts[id] == record then
          release(record); owner.source_contexts[id] = nil
        end
      end, 120000)
      return
    end
  end
end
function M.bind_client(owner, client, trace_id)
  if not trace_id then return end
  for _, record in pairs(owner.source_contexts or {}) do
    if record.trace_id == trace_id and record.cell_id == client.cell_id then record.client_id = client.client_id end
  end
end
function M.request(owner, cell_id, id, callback, client_id)
  local record = (owner.source_contexts or {})[id]
  if not record or record.cell_id ~= cell_id or not record.submitted or record.waiter
      or (client_id and record.client_id and client_id ~= record.client_id) then
    callback('Source context is absent, expired or already claimed'); return function() end
  end
  record.waiter = function(err, snapshot)
    record.waiter = nil
    owner.source_contexts[id] = nil
    record.snapshot = nil -- result ownership transfers to this action
    callback(err, snapshot)
  end
  if record.ready then record.waiter(record.error, record.snapshot) end
  return function()
    record.waiter = nil
    release(record)
    if owner.source_contexts then owner.source_contexts[id] = nil end
  end
end
function M.close(owner, cell_id, client_id)
  for id, record in pairs(owner.source_contexts or {}) do
    if (not cell_id or record.cell_id == cell_id) and (not client_id or record.client_id == client_id) then
      release(record); owner.source_contexts[id] = nil
    end
  end
end
return M
