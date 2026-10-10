-- Run from the repository root with the clean test init. No user configuration.
local jusi = require('jusi')
local output = assert(vim.env.JUSI_PROFILE_OUTPUT, 'set JUSI_PROFILE_OUTPUT to an existing directory')
local kind = vim.env.JUSI_PROFILE_KIND or 'vd'
local samples = tonumber(vim.env.JUSI_PROFILE_SAMPLES or '8')
assert(samples and samples >= 1 and samples % 1 == 0, 'samples must be a positive integer')
local instrument = vim.env.JUSI_PROFILE_INSTRUMENT ~= '0'
local rows = {}
local function now() return vim.uv.hrtime() end
local function measure(stage, started, sample)
  table.insert(rows, {stage=stage, ms=(now()-started)/1e6, sample=sample})
end
local function wait(predicate, message)
  assert(vim.wait(15000, predicate, 2), message)
end
local home = vim.fn.tempname()
vim.fn.mkdir(home, 'p')
vim.env.HOME = home
vim.env.VD_CONFIG = home .. '/.visidatarc'
vim.fn.writefile({'options.disp_menu = False'}, vim.env.VD_CONFIG)
local root = vim.fn.getcwd()
local code, needle, config
if kind == 'terminal' then
  vim.env.PYTHONPATH = root .. '/tests/fixtures/terminal_plugin'
  code, needle = {'%%terminal_fixture', 'profile'}, 'initial='
elseif kind == 'sqlite' then
  vim.env.PYTHONPATH = root .. '/tests/fixtures/sqlite_plugin'
  local database = home .. '/profile.sqlite'
  local setup = vim.system({'.venv/bin/python', '-c', "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('create table numbers(value text)'); c.execute(\"insert into numbers values ('profile_value')\"); c.commit(); c.close()", database}):wait(5000)
  assert(setup.code == 0, setup.stderr)
  config = home .. '/jusi.toml'
  vim.fn.writefile({'[sql.main]', 'provider = "sqlite"', 'path = "' .. database .. '"'}, config)
  code, needle = {'%%sql main', 'select value from numbers'}, 'profile_value'
else
  assert(kind == 'vd')
  code, needle = {'%%vd', "[{'value': 'profile_value'}]"}, 'profile_value'
end
vim.notify = function() end
jusi.setup({terminal_bridge_command={'.venv/bin/python', '-m', 'jusi', 'terminal-bridge'}})
local buf = vim.api.nvim_create_buf(false, true)
vim.api.nvim_win_set_buf(0, buf)
local function body(lines)
  local all = {'╭──'}
  vim.list_extend(all, lines)
  table.insert(all, '╰──')
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, all)
end
body({'1 + 1'})
local command = instrument and {'.venv/bin/python', 'scripts/profile-plugin-service.py', 'serve'}
  or {'.venv/bin/python', '-m', 'jusi', 'serve'}
if config then vim.list_extend(command, {'--config', config}) end
local service
local ok, err = xpcall(function()
  local t = now()
  service = jusi.start_service({buf=buf, command=command, timeout_ms=15000})
  wait(function() return jusi._sessions[buf] and jusi._sessions[buf].controller.transport_state == 'connected' end, 'connect')
  measure('service_connect', t)
  local session = jusi._sessions[buf]
  t = now()
  jusi.start_kernel(buf)
  wait(function() return session.controller.kernel_state == 'on' end, 'kernel start')
  measure('kernel_start', t)
  for sample=1,samples do
    local response, failure
    t = now()
    session.controller:execute(session.model:cell_at_row(1).id, function(result, e) response, failure = result, e end)
    wait(function() return response or failure end, 'plain execute')
    assert(not failure, vim.inspect(failure))
    measure('plain_execute_response', t, sample)
  end
  body(code)
  for sample=1,samples do
    t = now()
    jusi.execute(buf, 1)
    wait(function() return next(session.interactive.surfaces) ~= nil end, 'surface: ' .. vim.inspect(session.controller.failures))
    measure('execute_surface_projected', t, sample)
    local _, record = next(session.interactive.surfaces)
    wait(function() return table.concat(vim.api.nvim_buf_get_lines(record.buf, 0, -1, false), '\n'):find(needle, 1, true) end, 'first visible value')
    measure('execute_visible', t, sample)
    local quit = sample % 2 == 0
    t = now()
    if quit then vim.fn.chansend(record.job_id, kind == 'terminal' and '\3' or 'q')
    else jusi.close(buf, 1) end
    wait(function() return next(session.controller.clients) == nil and next(session.interactive.surfaces) == nil end, 'client close')
    measure(quit and 'application_quit_retired' or 'explicit_close_retired', t, sample)
    assert(session.controller.kernel_state == 'on')
  end
  t = now()
  jusi.stop_service(buf)
  wait(function() return service.state == 'stopped' and jusi._sessions[buf] == nil end, 'stop service')
  measure('service_stop', t)
end, debug.traceback)
if jusi._sessions[buf] then jusi._destroy_session(buf) end
vim.fn.delete(home, 'rf')
vim.fn.writefile({vim.json.encode({kind=kind, samples=samples, instrument=instrument,
  nvim=vim.version(), os=vim.uv.os_uname(), rows=rows})}, output .. '/frontend.json')
if not ok then error(err .. '\n' .. (service and service.stderr or '')) end
print('Profile written to ' .. output)
vim.cmd('qa!')
