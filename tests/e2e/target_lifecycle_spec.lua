local jusi = require("jusi")
local M = {}
local function wait_for(predicate, message)
  assert(vim.wait(12000, predicate, 10), message)
end
function M.run()
  local old_notify = vim.notify
  local messages = {}
  vim.notify = function(message) table.insert(messages, tostring(message)) end
  local command = { vim.fn.getcwd() .. "/.venv/bin/jusi", "serve" }
  local bridge = { vim.fn.getcwd() .. "/.venv/bin/jusi", "terminal-bridge" }
  local targets = {
    dev = { kind = "local", command = command, kernel_name = "python3" },
    bad = { kind = "local", command = command, kernel_name = "jusi-missing-kernel" },
  }
  jusi.setup({ targets = targets, kernel_name = "global-must-not-override-alias", terminal_bridge_command = bridge })
  local buffers, services = {}, {}
  local function new_notebook()
    local buf = vim.api.nvim_create_buf(false, true)
    table.insert(buffers, buf)
    vim.api.nvim_win_set_buf(0, buf)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "╭──", "input('stop me: ')", "╰──" })
    return buf
  end
  local function start(alias, buf)
    local ticket = jusi.start(alias, { buf = buf })
    if ticket and ticket.service then table.insert(services, ticket.service) end
    return ticket
  end
  local ok, failure = xpcall(function()
    local buf = new_notebook()
    assert(start("unknown", buf) == nil and jusi._sessions[buf] == nil)
    local cancelled = start("dev", buf)
    jusi.stop(buf)
    local ticket = start("dev", buf) -- retry before the cancelled process exit callback
    assert(jusi.start("dev", { buf = buf }) == ticket, "duplicate start created another operation")
    wait_for(function() return cancelled.service.state == "stopped" end, "pending service start was not cancelled")
    wait_for(function() return jusi._sessions[buf] and jusi._sessions[buf].controller.kernel_state == "on" and jusi._sessions[buf].controller.runtime_id ~= nil end, "composed local start failed")
    local session = jusi._sessions[buf]
    assert(session.service == ticket.service and session.target_alias == "dev")
    local kernel_id = session.controller.kernel_id
    jusi.start("dev", { buf = buf })
    assert(session.controller.kernel_id == kernel_id)
    local notebook_id = session.model.notebook_id
    jusi.restart(buf)
    wait_for(function() return session.model.notebook_id ~= notebook_id end, "restart ignored target's kernel profile")
    jusi.execute(buf, 1)
    wait_for(function() return session.controller.pending_input ~= nil end, "input request did not arrive")
    local output = session.presentation:buffer_for_cell(session.model:cell_at_row(1).id)
    local wins = vim.fn.win_findbuf(output)
    vim.api.nvim_set_current_win(wins[1])
    vim.cmd("JusiStop")
    jusi.stop(buf) -- duplicate pending stop shares the cleanup
    wait_for(function() return ticket.service.state == "stopped" and jusi._sessions[buf] == nil end, "stop from output did not clean owned service/kernel")
    assert(vim.api.nvim_buf_is_valid(buf) and not vim.api.nvim_buf_is_valid(output))
    jusi.stop(buf) -- idempotent

    local bad = start("bad", buf)
    wait_for(function() return bad.service.state == "stopped" and jusi._sessions[buf] == nil end, "failed kernel start leaked a local service")

    local during = start("dev", buf)
    wait_for(function() return during.kernel_starting == true end, "kernel start phase not reached")
    jusi.stop(buf)
    wait_for(function() return during.service.state == "stopped" and jusi._sessions[buf] == nil end, "stop during kernel start leaked runtime")

    local external = require("jusi.service.local").start({ command = command })
    table.insert(services, external)
    wait_for(function() return external.state == "running" end, "external target fixture did not start")
    targets.remote = { kind = "remote", base_url = external.base_url, kernel_name = "python3", terminal_bridge_command = bridge }
    jusi.setup({ targets = targets })
    start("remote", buf)
    wait_for(function() return jusi._sessions[buf] and jusi._sessions[buf].controller.kernel_state == "on" and jusi._sessions[buf].controller.runtime_id ~= nil end, "remote alias did not start kernel")
    local remote = jusi._sessions[buf]
    assert(remote.service == nil, "remote alias spawned/claimed a local service")
    local other = new_notebook()
    start("remote", other)
    wait_for(function() return jusi._sessions[other] == nil end, "foreign notebook target was not rejected")
    assert(remote.controller.kernel_state == "on")
    jusi.disconnect(buf)
    jusi.stop(buf)
    jusi.stop(buf) -- duplicate while reconnecting must not replace its callback
    wait_for(function() return jusi._sessions[buf] == nil end, "disconnected remote stop did not inspect then stop")
    assert(external.state == "running", "remote stop killed an unowned service")
    local health = vim.system({ "curl", "-fsS", external.base_url .. "/v1/health" }, { text = true }):wait(3000)
    assert(health.code == 0 and vim.json.decode(health.stdout).kernel.state == "off")
  end, debug.traceback)
  for _, buf in ipairs(buffers) do
    if jusi._sessions[buf] then jusi._destroy_session(buf) end
    if vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_delete(buf, { force = true }) end
  end
  for _, service in ipairs(services) do service:stop() end
  vim.wait(8000, function()
    for _, service in ipairs(services) do if service.state ~= "stopped" then return false end end
    return true
  end, 10)
  jusi.setup({ kernel_name = "python3", targets = { ["local"] = { kind = "local" } } })
  vim.notify = old_notify
  if not ok then error(failure .. "\nnotifications:\n" .. table.concat(messages, "\n")) end
  print("Target alias start/stop lifecycle passed")
end
return M
