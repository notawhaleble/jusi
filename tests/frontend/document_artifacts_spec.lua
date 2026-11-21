local M = {}
function M.run()
  local protocol = require('jusi.protocol')
  local function read(kind, name)
    return vim.json.decode(table.concat(vim.fn.readfile('protocol/fixtures/v1/' .. kind .. '/' .. name .. '.json'), '\n'))
  end
  for _, case in ipairs({ { 'open-document', 'open_document', 'document-provider-command' },
    { 'offer-document', 'offer_document', 'document-offer-token' },
    { 'open-shared-document', 'open_shared_document', 'shared-document-path' },
    { 'source-snapshot', 'source_snapshot', 'source-path-context' }, { 'close-document', 'close_document' } }) do
    assert(protocol.validate_editor_action(read('valid', case[1]), case[2]))
    if case[3] then assert(not protocol.validate_editor_action(read('invalid', case[3]), case[2])) end
  end
  assert(not protocol.validate_editor_action(read('invalid', 'shared-document-no-session'), 'open_shared_document'))
  assert(protocol.validate_document_offer_reply(read('valid', 'document-offer-reply')))
  assert(not protocol.validate_document_offer_reply(read('invalid', 'document-offer-reply-path')))
  assert(protocol.validate_editor_reply(read('valid', 'editor-artifact-reply')))
  assert(not protocol.validate_editor_reply(read('invalid', 'reply-negative-offset')))
  assert(not protocol.validate_editor_reply(read('invalid', 'reply-empty-chunk')))
  assert(protocol.validate_command(read('valid', 'followup-source-context'), 'followup'))
  assert(not protocol.validate_command(read('invalid', 'followup-source-path'), 'followup'))
  assert(protocol.validate_application_action(read('valid', 'application-reply-chunk')))
  local artifacts = require('jusi.artifacts')
  for _, name in ipairs({ 'open-document', 'offer-document', 'open-shared-document' }) do
    local value = read('valid', name .. '-tab')
    assert(protocol.validate_editor_action(value, value.action))
    assert(not protocol.validate_editor_action(read('invalid', name .. '-placement'), value.action))
  end
  local path = vim.fn.tempname()
  local bytes = 'binary\0with\255bytes' .. string.rep('abc', 10000)
  local f = assert(io.open(path, 'wb')); f:write(bytes); f:close()
  local wire = artifacts.encode({ metadata = { page = 7 }, files = { { path = path, name = 'page.png', size = #bytes, media_type = 'image/png' } } })
  local restored = artifacts.decode(wire)
  f = assert(io.open(restored.directory .. '/page.png', 'rb')); assert(f:read('*a') == bytes); f:close()
  restored.release(); os.remove(wire); os.remove(path)
  for _, case in ipairs({ {'valid', 'artifact-stream'}, {'valid', 'artifact-stream-unicode'}, {'invalid', 'artifact-stream-path'} }) do
    local kind, name = unpack(case)
    local fixture = read(kind, name)
    local stream = vim.fn.tempname(); local out = assert(io.open(stream, 'wb'))
    for _, record in ipairs(fixture.records) do out:write(vim.json.encode(record) .. '\n') end
    out:close()
    local success, decoded = pcall(artifacts.decode, stream)
    assert(success == (kind == 'valid'))
    if success then decoded.release() end
    os.remove(stream)
  end
end
return M
