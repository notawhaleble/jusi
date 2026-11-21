-- Finite, path-free artifact records. Binary bytes are staged locally, never
-- passed through Ex commands or interpreted as paths on another machine.
local M = {}
local BLOCK, MAX = 24576, 256 * 1024 * 1024
local function fields(value, names)
  if type(value) ~= 'table' or vim.tbl_count(value) ~= #names then return false end
  for _, name in ipairs(names) do if value[name] == nil then return false end end
  return true
end
local function manifest(value)
  assert(fields(value, { 'version', 'metadata', 'files' }), 'Invalid artifact fields')
  assert(type(value) == 'table' and value.version == 1 and type(value.metadata) == 'table'
    and #vim.json.encode(value.metadata) <= 16384 and type(value.files) == 'table'
    and vim.islist(value.files) and #value.files >= 1 and #value.files <= 16, 'Invalid artifact manifest')
  assert(not vim.islist(value.metadata) or next(value.metadata) == nil, 'Artifact metadata must be an object')
  if next(value.metadata) == nil then value.metadata = vim.empty_dict() end
  local names, total = {}, 0
  for _, item in ipairs(value.files) do
    assert(fields(item, { 'name', 'media_type', 'size' }), 'Invalid artifact file fields')
    assert(type(item.name) == 'string' and #item.name >= 1 and #item.name <= 255
      and item.name ~= '.' and item.name ~= '..' and not item.name:find('[/\\:%z\1-\31\127]')
      and not names[item.name], 'Invalid artifact name')
    assert(type(item.media_type) == 'string' and item.media_type:match('^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$'), 'Invalid artifact MIME')
    assert(type(item.size) == 'number' and item.size >= 0 and item.size % 1 == 0, 'Invalid artifact size')
    names[item.name] = true; total = total + item.size
  end
  assert(total <= MAX, 'Artifacts exceed 256 MiB')
  return value
end
local function private_file(path)
  local fd = assert(vim.uv.fs_open(path, 'wx', 384))
  vim.uv.fs_close(fd)
  return assert(io.open(path, 'wb'))
end
function M.read_manifest(path)
  local file = assert(io.open(path, 'rb'))
  local text = file:read(65537); file:close()
  assert(text and #text <= 65536, 'Document manifest exceeds 64 KiB')
  return manifest(vim.json.decode(text))
end
function M.reply(value)
  local path = vim.fn.tempname()
  local file = private_file(path)
  local ok, err = file:write(vim.json.encode(value)); file:close()
  if not ok then os.remove(path); error(err) end
  return path
end
function M.adopt_shared(root, client_id, artifact_id, header)
  -- IDs are validated by the action contract; paths come exclusively from the
  -- owned service handshake and the action's authoritative client metadata.
  local client = root.directory .. '/' .. vim.fn.sha256(client_id)
  local source = client .. '/' .. artifact_id
  for _, path in ipairs({ root.directory, client, source }) do
    local stat = vim.uv.fs_lstat(path)
    assert(stat and stat.type == 'directory', 'Shared staging is unavailable')
  end
  local names = {}
  for _, file in ipairs(header.files) do
    local stat = vim.uv.fs_lstat(source .. '/' .. file.name)
    assert(stat and stat.type == 'file' and stat.size == file.size, 'Invalid shared artifact file')
    names[file.name] = true
  end
  local scan = assert(vim.uv.fs_scandir(source))
  while true do
    local name = vim.uv.fs_scandir_next(scan)
    if not name then break end
    assert(names[name], 'Unexpected shared artifact file')
  end
  local directory = vim.fn.tempname()
  -- Move ownership out of service staging. Independent views may outlive the
  -- application, kernel and local service. Never hard-link a mutable original.
  if not vim.uv.fs_rename(source, directory) then
    assert(vim.fn.mkdir(directory, '', 448) == 1, 'Cannot adopt shared artifacts')
    local ok, err = pcall(function()
      for _, file in ipairs(header.files) do
        assert(vim.uv.fs_copyfile(source .. '/' .. file.name, directory .. '/' .. file.name, 1))
      end
    end)
    if not ok then vim.fn.delete(directory, 'rf'); error(err) end
  end
  header.directory = directory
  header.release = function() vim.fn.delete(directory, 'rf') end
  return header
end
function M.encode(snapshot)
  local path = vim.fn.tempname()
  local out = private_file(path)
  local ok, err = pcall(function()
    local files = {}
    for _, file in ipairs(snapshot.files) do
      table.insert(files, { name = file.name, media_type = file.media_type, size = file.size })
    end
    out:write(vim.json.encode(manifest({ version = 1, metadata = snapshot.metadata, files = files })) .. '\n')
    for index, file in ipairs(snapshot.files) do
      local stream = assert(io.open(file.path, 'rb'))
      local offset = 0
      local success, failure = pcall(function()
        while true do
          local bytes = stream:read(BLOCK)
          if not bytes then break end
          local data = vim.base64.encode(bytes)
          assert(out:write(vim.json.encode({ index = index - 1, offset = offset, data = data,
            sha256 = vim.fn.sha256(data) }) .. '\n'))
          offset = offset + #bytes
        end
        assert(offset == file.size, 'Artifact changed during capture')
      end)
      stream:close(); assert(success, failure)
    end
    assert(out:write('{"end":true}\n'))
  end)
  out:close()
  if not ok then os.remove(path); error(err) end
  return path
end
function M.decode(path)
  local directory = vim.fn.tempname()
  assert(vim.fn.mkdir(directory, '', 448) == 1, 'Cannot stage artifacts')
  local wire, streams = assert(io.open(path, 'rb')), {}
  local ok, result = pcall(function()
    local buffered = ''
    local function record()
      while not buffered:find('\n', 1, true) do
        assert(#buffered <= 65535, 'Artifact record too large')
        local chunk = wire:read(32768)
        assert(chunk, 'Incomplete artifact record'); buffered = buffered .. chunk
      end
      local stop = buffered:find('\n', 1, true)
      assert(stop <= 65536, 'Artifact record too large')
      local line = buffered:sub(1, stop - 1); buffered = buffered:sub(stop + 1)
      return vim.json.decode(line)
    end
    local header, offsets, last = manifest(record()), {}, 0
    for i, file in ipairs(header.files) do
      streams[i] = private_file(directory .. '/' .. file.name); offsets[i] = 0
    end
    while true do
      local item = record()
      if item['end'] == true and vim.tbl_count(item) == 1 then
        assert(buffered == '' and wire:read(1) == nil, 'Trailing artifact data')
        for i, file in ipairs(header.files) do assert(offsets[i] == file.size, 'Incomplete artifact') end
        break
      end
      assert(fields(item, { 'index', 'offset', 'data', 'sha256' }), 'Invalid artifact block fields')
      local index = type(item.index) == 'number' and item.index + 1 or 0
      assert(index % 1 == 0 and index >= last and streams[index] and offsets[index] == item.offset, 'Artifact offset mismatch')
      assert(type(item.data) == 'string' and vim.fn.sha256(item.data) == item.sha256, 'Artifact checksum mismatch')
      local bytes = vim.base64.decode(item.data)
      assert(#bytes > 0 and #bytes <= BLOCK and vim.base64.encode(bytes) == item.data, 'Invalid artifact block')
      offsets[index] = offsets[index] + #bytes; last = index
      assert(offsets[index] <= header.files[index].size, 'Artifact size exceeded')
      assert(streams[index]:write(bytes))
    end
    header.directory = directory
    header.release = function() vim.fn.delete(directory, 'rf') end
    return header
  end)
  wire:close()
  for _, stream in ipairs(streams) do stream:close() end
  if not ok then vim.fn.delete(directory, 'rf'); error(result) end
  return result
end
return M
