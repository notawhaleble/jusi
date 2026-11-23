local M = {}
local windows = require('jusi.presentation.window')
local function remove(surface)
  if surface.closed then return end
  if vim.api.nvim_buf_is_valid(surface.buf) then
    for _, win in ipairs(vim.fn.win_findbuf(surface.buf)) do
      if surface.source_buf and vim.api.nvim_buf_is_valid(surface.source_buf) then
        vim.api.nvim_win_set_buf(win, surface.source_buf)
      end
    end
    vim.api.nvim_buf_delete(surface.buf, { force = true })
  end
  surface.closed = true
end
local function close_slot(slots, slot)
  local surface = slots[slot]
  if surface and surface.lifetime == 'client' then
    surface:close()
    slots[slot] = nil
  end
end

function M.close_owned(slots)
  for _, surface in pairs(slots or {}) do
    if surface.lifetime == 'client' and not surface.closed then
      -- Client teardown must remove the overlay's split even when the hidden
      -- terminal has already been deleted. Explicit close_document still
      -- restores that terminal so the same client can continue.
      if vim.api.nvim_buf_is_valid(surface.buf) then windows.close_for_buffer(surface.buf) end
      surface:close()
    end
  end
end
function M.apply(content, opts)
  local slots = assert(opts.url_slots, 'Document actions require a client owner')
  local old = slots[content.slot]
  if content.action == 'close_document' then
    close_slot(slots, content.slot)
    return true
  end
  local host = require('jusi_neovim')
  if content.action == 'source_snapshot' then
    return { start = function(done)
      local cancel
      local function captured(err, snapshot)
        if err then done(false, nil, err); return end
        if snapshot.source_buf and vim.api.nvim_buf_is_valid(snapshot.source_buf) then
          if not old or old.buf ~= snapshot.source_buf then
            if old and old.lifetime == 'client' then old:close() end
            slots[content.slot] = { buf = snapshot.source_buf, lifetime = 'independent' }
          end
        end
        local ok, path = pcall(require('jusi.artifacts').encode, snapshot)
        snapshot.release()
        done(ok, ok and path or nil, not ok and path or nil)
      end
      if content.context_id ~= '' then
        cancel = require('jusi.source_context').request(opts.editing, opts.cell_id, content.context_id, captured, opts.client_id)
      elseif old and not old.closed and vim.api.nvim_buf_is_valid(old.buf) then
        cancel = host.snapshot(old.buf, captured)
      else captured('Source document is not open') end
      return cancel
    end }
  end
  assert(content.action == 'open_document' or content.action == 'offer_document' or content.action == 'open_shared_document', 'Unsupported document action')
  assert(type(host.provider_for) == 'function', 'Update the presentation host for document artifacts')
  local codec = require('jusi.artifacts')
  local artifact = content.action == 'open_document' and codec.decode(assert(opts.source_path))
    or codec.read_manifest(assert(opts.source_path))
  artifact.release = artifact.release or function() end
  local file = artifact.files[1]
  local provider = host.provider_for(file.media_type, content.provider)
  if not provider then artifact.release(); error('No compatible presentation provider for ' .. file.media_type) end
  local document_id = artifact.metadata.document_id
  if type(document_id) ~= 'string' or #document_id > 128 or document_id == '' then
    artifact.release(); error('Missing document identity')
  end
  local existing
  if content.lifetime == 'independent' then
    for _, buf in ipairs(vim.api.nvim_list_bufs()) do
      if vim.b[buf].jusi_independent_document_id == document_id and vim.b[buf].jusi_provider_id == provider then
        existing = buf
        break
      end
    end
  end
  if existing then
    artifact.release()
    if old and old.lifetime == 'client' then old:close() end
    slots[content.slot] = { buf = existing, lifetime = 'independent' }
    local win = windows.find(existing)
    if content.placement == 'tab' then
      if win then
        vim.api.nvim_set_current_win(win)
      else
        vim.cmd('tabnew')
        local placeholder = vim.api.nvim_get_current_buf()
        vim.api.nvim_set_current_buf(existing)
        vim.api.nvim_buf_delete(placeholder, { force = true })
      end
    elseif not win then windows.show(existing, { win = opts.win, split = 'right', enter = false }) end
    if content.action == 'offer_document' then
      return { start = function(done) done(true, codec.reply({ transport = 'reused' })) end }
    end
    return existing
  end
  local shared = opts.shared_artifacts and opts.shared_artifacts.token == content.shared_token
    and content.shared_token ~= '' and opts.shared_artifacts or nil
  if content.action == 'offer_document' then
    return { start = function(done)
      done(true, codec.reply({ transport = shared and 'shared' or 'http' }))
    end }
  elseif content.action == 'open_shared_document' then
    assert(shared, 'Shared artifacts were not negotiated for this service')
    artifact = codec.adopt_shared(shared, assert(opts.client_id), content.artifact_id, artifact)
  end
  return { start = function(done)
    local buf, completed, cancelled = nil, false, false
    local tab_win, placeholder
    local function cancel()
      cancelled = true
      local close_tab = tab_win and vim.api.nvim_win_is_valid(tab_win)
        and (vim.api.nvim_win_get_buf(tab_win) == placeholder or vim.api.nvim_win_get_buf(tab_win) == buf)
      if buf and vim.api.nvim_buf_is_valid(buf) then
        remove({ buf = buf, source_buf = content.lifetime == 'client' and opts.source_buf or nil })
      end
      if close_tab and vim.api.nvim_win_is_valid(tab_win) then
        pcall(vim.api.nvim_win_close, tab_win, true)
      end
      if placeholder and vim.api.nvim_buf_is_valid(placeholder) then
        pcall(vim.api.nvim_buf_delete, placeholder, { force = true })
      end
      artifact.release()
    end
    local ok, err = pcall(function()
      local win = opts.win
      assert(win and vim.api.nvim_win_is_valid(win), 'Source window is unavailable')
      if old and old.lifetime == 'client' then old:close(); slots[content.slot] = nil end
      if content.placement == 'tab' then
        vim.cmd('tabnew')
        win = vim.api.nvim_get_current_win()
        tab_win, placeholder = win, vim.api.nvim_get_current_buf()
      elseif content.lifetime == 'independent' then
        win = vim.api.nvim_open_win(vim.api.nvim_create_buf(false, true), false, { split = 'right', win = win })
      end
      vim.api.nvim_win_call(win, function()
        buf = host.open(provider, artifact.directory .. '/' .. file.name, {
          document_id = document_id,
          on_ready = function(failure, ready_buf)
            if cancelled or completed then return end
            completed = true
            if failure then cancel(); done(false, nil, failure); return end
            local surface = { buf = ready_buf, source_buf = content.lifetime == 'client' and opts.source_buf or nil,
              lifetime = content.lifetime, close = content.lifetime == 'client' and remove or nil }
            if content.lifetime == 'independent' then vim.b[ready_buf].jusi_independent_document_id = document_id end
            slots[content.slot] = surface
            if surface.source_buf and content.placement ~= 'tab' then
              vim.keymap.set('n', '<C-q>', function()
                if slots[content.slot] == surface then close_slot(slots, content.slot) end
              end, { buffer = ready_buf, silent = true, desc = 'Dismiss document and return to client' })
            end
            vim.api.nvim_create_autocmd('BufWipeout', { buffer = ready_buf, once = true, callback = function()
              surface.closed = true; artifact.release()
            end })
            done(true)
          end,
        })
        if content.lifetime == 'client' and content.placement ~= 'tab' then
          vim.b[buf].jusi_overlay_source = opts.source_buf
        end
      end)
      if placeholder and vim.api.nvim_buf_is_valid(placeholder) then
        vim.api.nvim_buf_delete(placeholder, { force = true })
      end
    end)
    if not ok then cancel(); done(false, nil, tostring(err)) end
    return cancel
  end }
end
return M
