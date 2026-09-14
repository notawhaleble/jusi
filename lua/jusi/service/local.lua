local M = {}
local LocalService = {}
LocalService.__index = LocalService

local counter = 0
local MAX_STDERR_BYTES = 16 * 1024

local function new_id()
  counter = counter + 1
  return string.format("svcproc_%x_%x", vim.uv.hrtime(), counter)
end

local function append_bounded(current, chunk)
  local combined = current .. (chunk or "")
  local truncated = #combined > MAX_STDERR_BYTES
  if truncated then
    combined = combined:sub(#combined - MAX_STDERR_BYTES + 1)
  end
  return combined, truncated
end

function LocalService:_failure(reason, message, retryable, extra)
  local details = extra or {}
  details.executable = self.command[1]
  return {
    trace_id = self.trace_id,
    layer = "frontend_service",
    operation = self.start_completed and self.stop_requested and "cleanup" or "service_start",
    reason = reason,
    message = message,
    retryable = retryable,
    scope = "supervisor",
    resource = { kind = "service_process", id = self.service_process_id },
    process = {
      pid = self.pid,
      exit_code = details.exit_code,
      signal = details.signal,
      stderr_excerpt = self.stderr,
      stderr_truncated = self.stderr_truncated,
    },
    details = details,
  }
end

function LocalService:_complete_start(ready, failure)
  if self.start_completed then
    return
  end
  self.start_completed = true
  if ready then
    self.ready = ready
    self.supervisor_id = ready.supervisor_id
    self.base_url = string.format("http://%s:%d", ready.host, ready.port)
    self.state = "running"
  else
    self.state = "stopped"
    self.failure = failure
  end
  if self.on_started then
    self.on_started(ready and self or nil, failure)
  end
end

function LocalService:stop(callback)
  if self.state == "stopped" then
    if callback then
      callback({ result = "already_absent" }, self.failure)
    end
    return
  end
  self.stop_requested = true
  self.on_stopped = callback or self.on_stopped
  if self.process then
    pcall(self.process.kill, self.process, 15)
  end
end

function LocalService:_on_exit(result)
  vim.schedule(function()
    local was_requested = self.stop_requested
    self.state = "stopped"
    local failure
    if not self.start_completed then
      local reason = self.pending_start_reason or (result.code == 0 and "readiness_failed" or "spawn_failed")
      local message = self.pending_start_message or "local service exited before readiness"
      failure = self:_failure(reason, message, self.pending_start_retryable ~= false, {
        exit_code = result.code,
        signal = result.signal,
      })
      self:_complete_start(nil, failure)
    elseif not was_requested then
      local reason = result.signal and result.signal ~= 0 and "process_signalled" or "process_exited"
      failure = self:_failure(reason, "local service exited unexpectedly", true, {
        exit_code = result.code,
        signal = result.signal,
      })
      self.failure = failure
      if self.on_failure then
        self.on_failure(failure)
      end
    elseif result.code ~= 0 and result.signal ~= 15 then
      failure = self:_failure("cleanup_incomplete", "local service did not stop cleanly", true, {
        exit_code = result.code,
        signal = result.signal,
      })
      self.failure = failure
    end
    if self.on_stopped then
      local stopped = self.on_stopped
      self.on_stopped = nil
      stopped({ result = failure and "failed" or "stopped" }, failure)
    end
  end)
end

function M.start(options, callback)
  local opts = options or {}
  local command = vim.deepcopy(opts.command or { "jusi", "serve" })
  vim.validate("command", command, "table")
  assert(#command > 0, "local service command must not be empty")
  vim.list_extend(command, { "--host", opts.host or "127.0.0.1", "--port", tostring(opts.port or 0), "--owner-stdin" })
  local service_process_id = new_id()
  local service = setmetatable({
    service_process_id = service_process_id,
    trace_id = "trace_" .. service_process_id,
    command = command,
    state = "starting",
    stderr = "",
    stderr_truncated = false,
    stdout = "",
    start_completed = false,
    stop_requested = false,
    on_started = callback,
    on_failure = opts.on_failure,
  }, LocalService)

  local spawned, process = pcall(vim.system, command, {
    text = true,
    stdin = true,
    stdout = function(error, data)
      if error and error ~= "" then
        local value, truncated = append_bounded(service.stderr, error)
        service.stderr = value
        service.stderr_truncated = service.stderr_truncated or truncated
      end
      if not data or service.start_completed then
        return
      end
      service.stdout = service.stdout .. data
      local newline = service.stdout:find("\n", 1, true)
      if not newline then
        return
      end
      local line = service.stdout:sub(1, newline - 1)
      vim.schedule(function()
        if service.start_completed then
          return
        end
        local ok, ready = pcall(vim.json.decode, line)
        if not ok or type(ready) ~= "table" or ready.status ~= "ready" or type(ready.host) ~= "string"
          or type(ready.port) ~= "number" or type(ready.supervisor_id) ~= "string"
        then
          service.pending_start_reason = "readiness_failed"
          service.pending_start_message = "local service emitted invalid readiness data"
          service.pending_start_retryable = false
          service.stop_requested = true
          pcall(service.process.kill, service.process, 15)
          return
        end
        service:_complete_start(ready, nil)
      end)
    end,
    stderr = function(error, data)
      local chunk = (error and error ~= "" and error or "") .. (data or "")
      local value, truncated = append_bounded(service.stderr, chunk)
      service.stderr = value
      service.stderr_truncated = service.stderr_truncated or truncated
    end,
  }, function(result)
    service:_on_exit(result)
  end)
  if not spawned then
    local failure = service:_failure("spawn_failed", "could not spawn local service: " .. tostring(process), false)
    service:_complete_start(nil, failure)
    return service
  end
  service.process = process
  service.pid = service.process.pid

  vim.defer_fn(function()
    if not service.start_completed then
      service.pending_start_reason = "readiness_failed"
      service.pending_start_message = "local service readiness timed out"
      service.pending_start_retryable = true
      service.stop_requested = true
      pcall(service.process.kill, service.process, 15)
      vim.defer_fn(function()
        if not service.start_completed and service.process then
          pcall(service.process.kill, service.process, 9)
        end
      end, 1000)
    end
  end, opts.timeout_ms or 8000)
  return service
end

M.LocalService = LocalService

return M
