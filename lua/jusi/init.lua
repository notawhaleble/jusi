local controller_module = require("jusi.controller")
local notebook = require("jusi.notebook")
local presentation_module = require("jusi.presentation")
local interactive_terminal = require("jusi.presentation.interactive_terminal")
local presentation_window = require("jusi.presentation.window")
local local_service = require("jusi.service.local")
local transport_module = require("jusi.transport.http_sse")

local M = {}

local config = {
  base_url = "http://127.0.0.1:8765",
  kernel_name = "python3",
  output_height = 12,
  service_command = { "jusi", "serve" },
  terminal_bridge_command = { "jusi", "terminal-bridge" },
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

local function cell_from_context(session, context_buf, row)
  local projected_cell_id = vim.b[context_buf].jusi_cell_id
  if type(projected_cell_id) == "string" and projected_cell_id ~= "" then
    return session.model:cell_by_id(projected_cell_id)
  end
  local cursor_row = row
  if cursor_row == nil then
    local window = vim.fn.bufwinid(context_buf)
    if window == -1 then
      return nil
    end
    cursor_row = vim.api.nvim_win_get_cursor(window)[1] - 1
  end
  return session.model:cell_at_row(cursor_row)
end

local function accepts_native_notebook(buf)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  if notebook.parser.detect_format(lines) ~= "legacy_0_x" then
    return true
  end
  notify(
    "legacy 0.x `##` notebook detected; conversion is not implemented yet, so open it with jusivim 0.x",
    vim.log.levels.ERROR
  )
  return false
end

local function new_presentation(model)
  return presentation_module.new({
    notebook_id = model.notebook_id,
    notebook_buf = model.buf,
    height = config.output_height,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  })
end

local function clients_for_cell(session, cell_id)
  local result = {}
  for client_id, client in pairs(session.controller.clients) do
    if client.cell_id == cell_id then
      table.insert(result, client_id)
    end
  end
  table.sort(result)
  return result
end

local function close_clients(session, client_ids, callback, index)
  local position = index or 1
  if position > #client_ids then
    callback(true)
    return nil
  end
  return session.controller:close_client(client_ids[position], function(_, failure)
    if failure then
      callback(false, failure)
      return
    end
    close_clients(session, client_ids, callback, position + 1)
  end)
end

local function retire_session(session)
  sessions[session.buf] = nil
  session.controller:close()
  session.presentation:close()
  session.interactive:close()
  session.model:detach()
end

local function replace_frontend_runtime(session, notebook_id)
  session.presentation:close()
  session.interactive:close()
  session.model:detach()
  local model = notebook.attach(session.buf, { notebook_id = notebook_id })
  local presentation = new_presentation(model)
  local interactive = interactive_terminal.new({
    base_url = session.base_url,
    command = config.terminal_bridge_command,
    height = config.output_height,
    notebook_buf = session.buf,
    notebook_id = notebook_id,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  })
  local callbacks = presentation:controller_callbacks()
  session.model = model
  session.presentation = presentation
  session.interactive = interactive
  session.controller.notebook = model
  session.controller.on_execution_started = callbacks.on_execution_started
  session.controller.on_output = callbacks.on_output
  session.controller.on_surface_created = function(surface)
    interactive:open(surface, session.controller.clients[surface.client_id])
  end
  session.controller.on_surface_closed = function(surface, payload)
    interactive:close_surface(payload.surface_id or (surface and surface.surface_id))
  end
  session.controller.on_resynchronized = function()
    interactive:reconcile(session.controller.surfaces, session.controller.clients)
  end
  session.controller.executions = {}
  return model
end

function M.connect(options)
  local opts = options or {}
  local buf = opts.buf or vim.api.nvim_get_current_buf()
  if not accepts_native_notebook(buf) then
    return nil
  end
  local existing = sessions[buf]
  if existing then
    existing.controller:connect()
    return existing
  end

  local model = notebook.attach(buf)
  local presentation = new_presentation(model)
  local base_url = opts.base_url or config.base_url
  local interactive = interactive_terminal.new({
    base_url = base_url,
    command = config.terminal_bridge_command,
    height = config.output_height,
    notebook_buf = buf,
    notebook_id = model.notebook_id,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  })
  local callbacks = presentation:controller_callbacks()
  local transport = opts.transport or transport_module.new({ base_url = base_url })
  local controller
  controller = controller_module.new({
    notebook = model,
    transport = transport,
    kernel_name = opts.kernel_name or config.kernel_name,
    on_execution_started = callbacks.on_execution_started,
    on_output = callbacks.on_output,
    on_surface_created = function(surface)
      interactive:open(surface, controller.clients[surface.client_id])
    end,
    on_surface_closed = function(surface, payload)
      interactive:close_surface(payload.surface_id or (surface and surface.surface_id))
    end,
    on_resynchronized = function()
      interactive:reconcile(controller.surfaces, controller.clients)
    end,
    on_failure = function(failure)
      notify(failure_text(failure), vim.log.levels.ERROR)
    end,
  })
  local session = {
    buf = buf,
    model = model,
    presentation = presentation,
    interactive = interactive,
    controller = controller,
    base_url = base_url,
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
  if not accepts_native_notebook(buf) then
    return nil
  end
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
        retire_session(session)
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

function M.restart(buf)
  local session = require_session(buf)
  if not session then
    return nil
  end
  session.controller.kernel_name = config.kernel_name
  local next_notebook_id = notebook.new_notebook_id()
  return session.controller:restart_notebook(next_notebook_id, function(response, failure)
    local teardown_completed = response ~= nil
      or (failure and failure.details and failure.details.teardown_completed == true)
    if teardown_completed then
      replace_frontend_runtime(session, next_notebook_id)
    end
    if failure then
      notify(failure_text(failure), vim.log.levels.ERROR)
    elseif response then
      notify("notebook restarted: " .. response.kernel.kernel_id)
    end
  end)
end

function M.execute(buf, row)
  local context_buf = buf or vim.api.nvim_get_current_buf()
  local session = require_session(context_buf)
  if not session then
    return nil
  end
  local cell = cell_from_context(session, context_buf, row)
  if not cell then
    notify("cursor is not inside a cell", vim.log.levels.ERROR)
    return nil
  end
  local function execute_cell()
    return session.controller:execute(cell.id, function(_, failure)
      if failure then
        notify(failure_text(failure), vim.log.levels.ERROR)
      end
    end)
  end
  local client_ids = clients_for_cell(session, cell.id)
  if #client_ids == 0 then
    return execute_cell()
  end
  return close_clients(session, client_ids, function(closed, failure)
    if not closed then
      notify(failure_text(failure), vim.log.levels.ERROR)
      return
    end
    execute_cell()
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

function M.close(buf, row)
  local context_buf = buf or vim.api.nvim_get_current_buf()
  local session = require_session(context_buf)
  if not session then
    return nil
  end
  local cell = cell_from_context(session, context_buf, row)
  if not cell then
    notify("cursor is not inside a cell", vim.log.levels.ERROR)
    return nil
  end
  local client_ids = clients_for_cell(session, cell.id)
  local output_buf = session.presentation:buffer_for_cell(cell.id)
  if output_buf then
    session.presentation:close_cell(cell.id)
  end
  if #client_ids == 0 then
    if not output_buf then
      notify("cell has no execution artifact", vim.log.levels.WARN)
      return nil
    end
    return true
  end
  return close_clients(session, client_ids, function(closed, failure)
    if not closed then
      notify(failure_text(failure), vim.log.levels.ERROR)
    end
  end)
end

local function focus_notebook_cell(session, cell)
  local window = presentation_window.show(session.buf, {
    anchor_buf = vim.api.nvim_get_current_buf(),
    enter = true,
    split = "above",
  })
  local snapshot = session.model:cell_snapshot(cell)
  if snapshot then
    local row = math.min(snapshot.body_start_row, snapshot.close_row)
    vim.api.nvim_win_set_cursor(window, { row + 1, 0 })
  end
  return session.buf
end

function M.toggle_focus(buf, row)
  local context_buf = buf or vim.api.nvim_get_current_buf()
  local session = require_session(context_buf)
  if not session then
    return nil
  end
  local cell = cell_from_context(session, context_buf, row)
  if not cell then
    notify("cursor is not inside a cell", vim.log.levels.ERROR)
    return nil
  end
  if context_buf ~= session.buf then
    return focus_notebook_cell(session, cell)
  end
  local artifact_buf = session.interactive:buffer_for_cell(cell.id)
    or session.presentation:buffer_for_cell(cell.id)
  if not artifact_buf then
    notify("cell has no execution artifact", vim.log.levels.WARN)
    return nil
  end
  presentation_window.show(artifact_buf, {
    anchor_buf = session.buf,
    height = config.output_height,
    enter = true,
  })
  return artifact_buf
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
  retire_session(session)
  if session.service then
    session.service:stop(function(_, failure)
      if failure then
        notify(failure_text(failure), vim.log.levels.ERROR)
      end
    end)
  end
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
  vim.api.nvim_create_user_command("JusiRestart", function()
    M.restart()
  end, {})
  vim.api.nvim_create_user_command("JusiExecute", function()
    M.execute()
  end, {})
  vim.api.nvim_create_user_command("JusiStopKernel", function()
    M.stop_kernel()
  end, {})
  vim.api.nvim_create_user_command("JusiClose", function()
    M.close()
  end, {})
  vim.api.nvim_create_user_command("JusiToggleFocus", function()
    M.toggle_focus()
  end, {})
  vim.api.nvim_create_user_command("JusiDisconnect", function()
    M.disconnect()
  end, {})
end

function M.setup(options)
  local opts = options or {}
  -- `ftdetect/` covers normal plugin loading; this also covers explicit
  -- `runtime plugin/jusi.lua` loading from a clean Neovim invocation.
  vim.filetype.add({ extension = { vipynb = "jusi" } })
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
    if opts.terminal_bridge_command == nil and opts.service_command[2] == "serve" then
      config.terminal_bridge_command = { opts.service_command[1], "terminal-bridge" }
    end
  end
  if opts.terminal_bridge_command ~= nil then
    vim.validate("terminal_bridge_command", opts.terminal_bridge_command, "table")
    assert(#opts.terminal_bridge_command > 0, "terminal_bridge_command must not be empty")
    config.terminal_bridge_command = vim.deepcopy(opts.terminal_bridge_command)
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
