local sse = require("jusi.transport.sse")
local process = require("jusi.transport.process")

local M = {}
local Transport = {}
Transport.__index = Transport

local transport_counter = 0
local STATUS_MARKER = "\n__JUSI_HTTP_STATUS__:"

local function new_transport_id()
  transport_counter = transport_counter + 1
  return string.format("trn_%x_%x", vim.uv.hrtime(), transport_counter)
end

local function trim_trailing_slash(value)
  return value:gsub("/+$", "")
end

local function decode_json(value)
  local ok, decoded = pcall(vim.json.decode, value)
  if ok and type(decoded) == "table" then
    return decoded
  end
  return nil
end

function Transport:_failure(operation, trace_id, reason, message, details)
  return {
    trace_id = trace_id,
    layer = "frontend_transport",
    operation = operation,
    reason = reason,
    message = message,
    retryable = reason == "timeout" or reason == "unreachable" or reason == "channel_closed",
    scope = "transport",
    resource = { kind = "transport", id = self.transport_id },
    details = details or {},
  }
end

function Transport:request(method, path, payload, options, callback)
  vim.validate("method", method, "string")
  vim.validate("path", path, "string")
  vim.validate("callback", callback, "function")
  local opts = options or {}
  local timeout_ms = opts.timeout_ms or self.request_timeout_ms
  local operation = opts.operation or "inspect"
  local trace_id = opts.trace_id or ""
  local body = payload and vim.json.encode(payload) or nil
  local command = {
    self.curl,
    "--silent",
    "--show-error",
    "--request",
    method,
    "--connect-timeout",
    string.format("%.3f", self.request_timeout_ms / 1000),
    "--max-time",
    string.format("%.3f", timeout_ms / 1000),
    "--header",
    "Accept: application/json",
    "--write-out",
    STATUS_MARKER .. "%{http_code}",
  }
  if body then
    vim.list_extend(command, { "--header", "Content-Type: application/json", "--data-binary", "@-" })
  end
  table.insert(command, self.base_url .. path)

  local handle
  handle = process.start(command, { stdin = body }, function(result)
    vim.schedule(function()
      self.requests[handle] = nil
      local stdout = result.stdout or ""
      local marker_start = nil
      local search_from = 1
      while true do
        local found = stdout:find(STATUS_MARKER, search_from, true)
        if not found then
          break
        end
        marker_start = found
        search_from = found + 1
      end
      local status = nil
      local response_body = stdout
      if marker_start then
        response_body = stdout:sub(1, marker_start - 1)
        status = tonumber(stdout:sub(marker_start + #STATUS_MARKER))
      end
      local decoded = decode_json(response_body)
      if result.code ~= 0 then
        local reason = result.code == 28 and "timeout" or "unreachable"
        callback(nil, self:_failure(operation, trace_id, reason, result.stderr or "curl request failed", {
          exit_code = result.code,
          stderr = result.stderr or "",
        }))
        return
      end
      if not status then
        callback(nil, self:_failure(operation, trace_id, "protocol_violation", "HTTP response has no status marker"))
        return
      end
      if not decoded then
        callback(nil, self:_failure(operation, trace_id, "protocol_violation", "HTTP response is not a JSON object", {
          status = status,
        }))
        return
      end
      if status < 200 or status >= 300 or decoded.ok ~= true then
        if type(decoded.failure) == "table" then
          callback(nil, decoded.failure)
        else
          callback(nil, self:_failure(operation, trace_id, "protocol_violation", "HTTP request failed without a typed failure", {
            status = status,
            response = decoded,
          }))
        end
        return
      end
      callback(decoded, nil)
    end)
  end)
  self.requests[handle] = true
  return handle
end

function Transport:cancel_requests()
  for handle in pairs(self.requests) do
    handle:kill()
  end
end

function Transport:connect_events(after, callbacks)
  vim.validate("after", after, "number")
  local handlers = callbacks or {}
  local connection = {
    closed = false,
    transport_id = self.transport_id,
  }
  local opened = false
  local function notify_open()
    if opened or connection.closed then
      return
    end
    opened = true
    if handlers.on_open then
      vim.schedule(function()
        if not connection.closed then
          handlers.on_open()
        end
      end)
    end
  end
  local event_parser = sse.new(function(record)
    notify_open()
    local decoded = decode_json(record.data)
    if not decoded then
      if handlers.on_error then
        vim.schedule(function()
          handlers.on_error(self:_failure("connect_events", "", "protocol_violation", "SSE data is not JSON"))
        end)
      end
      return
    end
    if handlers.on_event then
      vim.schedule(function()
        if not connection.closed then
          handlers.on_event(decoded, record)
        end
      end)
    end
  end, function()
    notify_open()
  end)
  local command = {
    self.curl,
    "--silent",
    "--show-error",
    "--no-buffer",
    "--connect-timeout", tostring(self.request_timeout_ms / 1000),
    "--speed-limit", "1",
    "--speed-time", "10",
    "--header",
    "Accept: text/event-stream",
    self.base_url .. "/v1/events?after=" .. tostring(after) .. (handlers.editor_id and ("&editor_id=" .. handlers.editor_id) or ""),
  }
  connection.process = process.start(command, {
    stdout = function(error, data)
      if error and error ~= "" and handlers.on_error then
        vim.schedule(function()
          handlers.on_error(self:_failure("connect_events", "", "channel_closed", error))
        end)
      end
      event_parser:feed(data)
    end,
  }, function(result)
    event_parser:finish()
    vim.schedule(function()
      if connection.closed then
        return
      end
      connection.closed = true
      self:cancel_requests()
      if handlers.on_close then
        handlers.on_close(result.code, result.stderr or "")
      end
      if result.code ~= 0 and handlers.on_error then
        handlers.on_error(self:_failure("connect_events", "", "channel_closed", result.stderr or "event stream closed", {
          exit_code = result.code,
        }))
      end
    end)
  end)
  local transport = self
  function connection:close()
    if self.closed then
      return
    end
    self.closed = true
    transport:cancel_requests()
    if self.process then
      pcall(self.process.kill, self.process, 15)
    end
  end
  return connection
end

function M.new(options)
  local opts = options or {}
  vim.validate("base_url", opts.base_url, "string")
  return setmetatable({
    base_url = trim_trailing_slash(opts.base_url),
    curl = opts.curl or "curl",
    request_timeout_ms = opts.request_timeout_ms or 15000,
    transport_id = opts.transport_id or new_transport_id(),
    requests = {},
  }, Transport)
end

M.Transport = Transport

return M
