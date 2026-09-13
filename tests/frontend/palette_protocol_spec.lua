local M = {}
function M.run()
  local protocol = require('jusi.protocol')
  local function read(path) return vim.json.decode(table.concat(vim.fn.readfile('protocol/fixtures/v1/' .. path), '\n')) end
  assert(protocol.validate_health_response(read('valid/health-palette.json')))
  for _, suffix in ipairs({'duplicate','value','fields'}) do
    assert(not protocol.validate_health_response(read('invalid/health-palette-' .. suffix .. '.json')))
  end
  assert(read('scenarios/palette.json').commands[3]:sub(1,2) == 'J!')
end
return M
