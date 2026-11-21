local M = {}
function M.run()
  local codec = require('jusi.artifacts')
  for _, cross_device in ipairs({ false, true }) do
    local root = { directory = vim.fn.tempname() }
    local client, id = 'client-one', string.rep('a', 32)
    local parent = root.directory .. '/' .. vim.fn.sha256(client)
    local stage = parent .. '/' .. id
    vim.fn.mkdir(stage, 'p', 448)
    local bytes = 'binary\0page\255'
    local f = assert(io.open(stage .. '/page.pdf', 'wb')); f:write(bytes); f:close()
    local header = { version = 1, metadata = { document_id = 'fixture' },
      files = { { name = 'page.pdf', media_type = 'application/pdf', size = #bytes } } }
    assert(not pcall(codec.adopt_shared, root, 'other-client', id, header), 'Another client adopted the snapshot')
    assert(vim.fn.filereadable(stage .. '/page.pdf') == 1)
    local rename = vim.uv.fs_rename
    if cross_device then vim.uv.fs_rename = function() return nil, 'EXDEV' end end
    local ok, result = pcall(codec.adopt_shared, root, client, id, header)
    vim.uv.fs_rename = rename
    assert(ok, result)
    vim.fn.delete(root.directory, 'rf') -- service/client closure cannot remove an adopted document
    f = assert(io.open(result.directory .. '/page.pdf', 'rb')); assert(f:read('*a') == bytes); f:close()
    result.release(); assert(vim.fn.isdirectory(result.directory) == 0)
  end
  local root = { directory = vim.fn.tempname() }
  local stage = root.directory .. '/' .. vim.fn.sha256('client') .. '/' .. string.rep('b', 32)
  vim.fn.mkdir(stage, 'p', 448)
  local outside = vim.fn.tempname(); vim.fn.writefile({ 'source' }, outside)
  assert(vim.uv.fs_symlink(outside, stage .. '/page.pdf'))
  local header = { version = 1, metadata = {}, files = { { name = 'page.pdf', media_type = 'application/pdf', size = 7 } } }
  assert(not pcall(codec.adopt_shared, root, 'client', string.rep('b', 32), header), 'Symlink was adopted')
  assert(vim.fn.filereadable(outside) == 1)
  vim.fn.delete(root.directory, 'rf'); os.remove(outside)
end
return M
