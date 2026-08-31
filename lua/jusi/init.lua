local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")
local presentation_module = require("jusi.presentation")
local local_service = require("jusi.service.local")
local transport_module = require("jusi.transport.http_sse")

local M = {}

local config = {
  base_url = "http://127.0.0.1:8765",
  kernel_name = "python3",
  output_height = 12,
  service_command = { "jusi", "serve" },
  service_timeout_ms = 8000,
}
local sessions = {}
local pending_services = {}
local commands_created = false

local function notify(message, level)
  vim.notify(message, level or vim.log.levels.INFO, { title = "Jusi" })
end

local function failure_text(failure)
  if type(failure) ~= "table" then
    return tostring(failure)
  end
  local origin = table.concat({ failure.layer or "unknown", failure.operation or "unknown", failure.reason or "unknown" }, "/")
  local trace = failure.trace_id and failure.trace_id ~= "" and (" trace=" .. failure.trace_id) or ""
  return origin .. ": " .. tostring(failure.message or "failure") .. trace
end

local function current_session(buf)
  local buffer = buf or vim.api.nvim_get_current_buf()
  local direct = sessions[buffer]
  if direct then
    return direct
  end
  local notebook_id = vim.b[buffer].jusi_notebook_id
  if notebook_id then
    for _, session in pairs(sessions) do
      if session.model.notebook_id == notebook_id then
        return session
      end
    end
  end
  return nil
end

local function require_session(buf)
  local session = current_session(buf)
  if not session then
    notify("current buffer is not connected; run :JusiConnect", vim.log.levels.ERROR)
  end
  return session
end

function M.connect(options)
  local opts = options or {}
  local buf = opts.buf or vim.api.nvim_get_current_buf()
  local existing = sessions[buf]
  if existing then
    existing.controller:connect()
    return existing
  end

  local model = notebook.attach(buf)
  local presentation = presentation_module.new({
    notebook_id = model.notebook_id,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  })
  local callbacks = presentation:controller_callbacks()
  local transport = opts.transport or transport_module.new({ base_url = opts.base_url or config.base_url })
  local controller = controller_module.new({
    notebook = model,
    transport = transport,
    kernel_name = opts.kernel_name or config.kernel_name,
    on_execution_started = callbacks.on_execution_started,
    on_output = callbacks.on_output,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  })
  local session = {
    buf = buf,
    model = model,
    presentation = presentation,
    controller = controller,
    base_url = opts.base_url or config.base_url,
  }
  sessions[buf] = session
  vim.api.nvim_create_autocmd("BufWipeout", {
    buffer = buf,
    once = true,
    callback = function()
      M._destroy_session(buf)
    end,
  })
  controller:connect(function(connected, failure)
    if connected then
      notify("event stream connected")
    end
  end)
  return session
end

function M.start_service(options)
  local opts = options or {}
  local buf = opts.buf or vim.api.nvim_get_current_buf()
  if sessions[buf] or pending_services[buf] then
    notify("buffer already has a frontend session or pending service", vim.log.levels.ERROR)
    return nil
  end
  local service
  service = local_service.start({
    command = opts.command or config.service_command,
    timeout_ms = opts.timeout_ms or config.service_timeout_ms,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  }, function(started, failure)
    pending_services[buf] = nil
    if failure then
      notify(failure_text(failure), vim.log.levels.ERROR)
      return
    end
    if not vim.api.nvim_buf_is_valid(buf) then
      started:stop()
      return
    end
    local session = M.connect({ buf = buf, base_url = started.base_url })
    session.service = started
    notify("local service ready: " .. started.supervisor_id)
  end)
  if service.state ~= "stopped" then
    pending_services[buf] = service
    vim.api.nvim_create_autocmd("BufWipeout", {
      buffer = buf,
      once = true,
      callback = function()
        local pending = pending_services[buf]
        pending_services[buf] = nil
        if pending then
          pending:stop()
        end
      end,
    })
  end
  return service
end

function M.stop_service(buf)
  local session = current_session(buf)
  if not session or not session.service then
    notify("current buffer has no owned local service", vim.log.levels.ERROR)
    return nil
  end
  local service = session.service
  local function stop_process()
    session.controller:close()
    service:stop(function(result, failure)
      if failure then
        notify(failure_text(failure), vim.log.levels.ERROR)
      else
        session.service = nil
        notify("local service stopped: " .. result.result)
      end
    end)
  end
  if session.controller.kernel_state == "on" and session.controller.kernel_id then
    return session.controller:stop_kernel(function(response, failure)
      if failure then
        notify(failure_text(failure), vim.log.levels.ERROR)
        return
      end
      if response then
        stop_process()
      end
    end)
  end
  stop_process()
  return service
end

function M.start_kernel(buf)
  local session = require_session(buf)
  if not session then
    return nil
  end
  return session.controller:start_kernel(function(response, failure)
    if failure then
      notify(failure_text(failure), vim.log.levels.ERROR)
    elseif response then
      notify("kernel on: " .. response.kernel.kernel_id)
    end
  end)
end

function M.execute(buf, row)
  local session = require_session(buf)
  if not session then
    return nil
  end
  local cursor_row = row
  if cursor_row == nil then
    cursor_row = vim.api.nvim_win_get_cursor(0)[1] - 1
  end
  local cell = session.model:cell_at_row(cursor_row)
  if not cell then
    notify("cursor is not inside a cell", vim.log.levels.ERROR)
    return nil
  end
  return session.controller:execute(cell.id, function(_, failure)
    if failure then
      notify(failure_text(failure), vim.log.levels.ERROR)
    end
  end)
end

function M.stop_kernel(buf)
  local session = require_session(buf)
  if not session then
    return nil
  end
  return session.controller:stop_kernel(function(response, failure)
    if failure then
      notify(failure_text(failure), vim.log.levels.ERROR)
    elseif response then
      notify("kernel off: " .. response.cleanup.result)
    end
  end)
end

function M.open_output(buf, row)
  local session = require_session(buf)
  if not session then
    return nil
  end
  local cursor_row = row
  if cursor_row == nil then
    cursor_row = vim.api.nvim_win_get_cursor(0)[1] - 1
  end
  local cell = session.model:cell_at_row(cursor_row)
  if not cell then
    notify("cursor is not inside a cell", vim.log.levels.ERROR)
    return nil
  end
  local output_buf = session.presentation:buffer_for_cell(cell.id)
  if not output_buf then
    notify("cell has no output surface", vim.log.levels.WARN)
    return nil
  end
  vim.cmd("botright " .. tostring(config.output_height) .. "split")
  vim.api.nvim_win_set_buf(0, output_buf)
  return output_buf
end

function M.disconnect(buf)
  local buffer = buf or vim.api.nvim_get_current_buf()
  local session = current_session(buffer)
  if not session then
    return false
  end
  session.controller:close()
  return true
end

function M._destroy_session(buf)
  local session = current_session(buf)
  if not session then
    return false
  end
  sessions[session.buf] = nil
  session.controller:close()
  if session.service then
    session.service:stop(function(_, failure)
      if failure then
        notify(failure_text(failure), vim.log.levels.ERROR)
      end
    end)
  end
  session.presentation:close()
  session.model:detach()
  return true
end

local function create_commands()
  if commands_created then
    return
  end
  commands_created = true
  vim.api.nvim_create_user_command("JusiConnect", function(command)
    M.connect({ base_url = command.args ~= "" and command.args or nil })
  end, { nargs = "?" })
  vim.api.nvim_create_user_command("JusiServiceStart", function()
    M.start_service()
  end, {})
  vim.api.nvim_create_user_command("JusiServiceStop", function()
    M.stop_service()
  end, {})
  vim.api.nvim_create_user_command("JusiStartKernel", function()
    M.start_kernel()
  end, {})
  vim.api.nvim_create_user_command("JusiExecute", function()
    M.execute()
  end, {})
  vim.api.nvim_create_user_command("JusiStopKernel", function()
    M.stop_kernel()
  end, {})
  vim.api.nvim_create_user_command("JusiOpenOutput", function()
    M.open_output()
  end, {})
  vim.api.nvim_create_user_command("JusiDisconnect", function()
    M.disconnect()
  end, {})
end

function M.setup(options)
  local opts = options or {}
  if opts.base_url ~= nil then
    vim.validate("base_url", opts.base_url, "string")
    config.base_url = opts.base_url
  end
  if opts.kernel_name ~= nil then
    vim.validate("kernel_name", opts.kernel_name, "string")
    config.kernel_name = opts.kernel_name
  end
  if opts.output_height ~= nil then
    vim.validate("output_height", opts.output_height, "number")
    assert(opts.output_height >= 1 and opts.output_height % 1 == 0, "output_height must be a positive integer")
    config.output_height = opts.output_height
  end
  if opts.service_command ~= nil then
    vim.validate("service_command", opts.service_command, "table")
    assert(#opts.service_command > 0, "service_command must not be empty")
    config.service_command = vim.deepcopy(opts.service_command)
  end
  if opts.service_timeout_ms ~= nil then
    vim.validate("service_timeout_ms", opts.service_timeout_ms, "number")
    assert(opts.service_timeout_ms >= 1, "service_timeout_ms must be positive")
    config.service_timeout_ms = opts.service_timeout_ms
  end
  create_commands()
end

M._sessions = sessions

return M
