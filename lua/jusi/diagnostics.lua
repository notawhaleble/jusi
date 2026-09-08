-- Editor-session diagnostics, independent of notebook and service lifetimes.
local M = {}
local records = {}
local LIMIT = 50
local fields = {
  "failure_id", "trace_id", "layer", "operation", "reason", "message",
  "retryable", "scope", "resource", "occurred_at", "caused_by_failure_id",
}
-- Deliberately exclude arbitrary payload/config/environment/argv fields.
local detail_fields = {
  "path", "line", "column", "executable", "kernel_id", "execution_id",
  "client_id", "plugin_worker_id", "runtime_id", "notebook_id", "transport_id",
  "surface_id", "discovery_id", "supervisor_id", "next_notebook_id",
  "teardown_completed", "side_effects_may_have_occurred", "kernel_state",
  "code_bytes", "code_line_count", "adapter_modules", "attestation_count",
  "entry_point", "distribution", "earliest_cursor", "latest_cursor",
}

local function bounded(value, depth, budget)
  if budget.left <= 0 then return "[truncated]" end
  if type(value) == "string" then
    local limit = math.min(16384, budget.left)
    budget.left = budget.left - math.min(#value, limit)
    return #value > limit and (value:sub(1, limit) .. " [truncated]") or value
  end
  if type(value) == "number" or type(value) == "boolean" then return value end
  if type(value) ~= "table" or depth >= 4 then return nil end
  local copy, count = {}, 0
  for key, item in pairs(value) do
    count = count + 1
    if count > 32 or budget.left <= 0 then copy.truncated = true; break end
    if type(key) == "number" or (type(key) == "string" and #key <= 64) then
      budget.left = budget.left - 64
      copy[key] = bounded(item, depth + 1, budget)
    end
  end
  return copy
end

function M.record(failure)
  if type(failure) ~= "table" then return end
  local record, budget = {}, { left = 32768 }
  for _, key in ipairs(fields) do record[key] = bounded(failure[key], 0, budget) end
  record.process = {}
  for _, key in ipairs({ "pid", "exit_code", "signal", "stderr_excerpt", "stderr_truncated" }) do
    record.process[key] = bounded((failure.process or {})[key], 0, budget)
  end
  record.details = {}
  for _, key in ipairs(detail_fields) do
    record.details[key] = bounded((failure.details or {})[key], 0, budget)
  end
  -- HTTP completion and SSE can report the same failure. Keep distinct causes.
  for _, previous in ipairs(records) do
    if vim.deep_equal(previous, record) then return previous end
  end
  records[#records + 1] = record
  if #records > LIMIT then table.remove(records, 1) end
  return record
end

local function one_line(value)
  return tostring(value):gsub("[%c]", " "):sub(1, 300)
end

function M.summary(failure)
  if type(failure) ~= "table" then return one_line(failure) end
  local record = M.record(failure)
  local origin = table.concat({ record.layer or "unknown", record.operation or "unknown", record.reason or "unknown" }, "/")
  local message = origin .. ": " .. one_line(record.message or "failure")
  if record.details.path then message = message .. " (" .. one_line(record.details.path) .. ")" end
  local process = record.process
  if process.exit_code ~= nil then message = message .. " exit=" .. tostring(process.exit_code) end
  if process.signal and process.signal ~= 0 then message = message .. " signal=" .. tostring(process.signal) end
  if record.layer == "frontend_service" and process.stderr_excerpt then
    local last = process.stderr_excerpt:match("([^\r\n]+)[\r\n]*$")
    if last then message = message .. ": " .. one_line(last) end
  end
  return message .. (record.trace_id and (" — :JusiTrace " .. record.trace_id) or " — :JusiTrace")
end

function M.find(trace_id)
  local result = {}
  local selected = trace_id
  if not selected or selected == "" then selected = records[#records] and records[#records].trace_id end
  for _, record in ipairs(records) do
    if record.trace_id == selected then result[#result + 1] = vim.deepcopy(record) end
  end
  return result
end

function M.trace_ids()
  local ids, seen = {}, {}
  for index = #records, 1, -1 do
    local id = records[index].trace_id
    if id and not seen[id] then ids[#ids + 1] = id; seen[id] = true end
  end
  return ids
end

function M.open(trace_id)
  local found = M.find(trace_id)
  if #found == 0 then
    vim.notify("No retained Jusi failure" .. (trace_id and trace_id ~= "" and (" for " .. trace_id) or "")
      .. "; history contains the last 50 failures received in this Neovim session.", vim.log.levels.INFO)
    return
  end
  local lines = { "Jusi failure details", "Session-local history; received order, not a complete backend trace.", "" }
  local function field(label, value)
    if value ~= nil then
      local display = type(value) == "table" and vim.inspect(value) or tostring(value)
      for index, line in ipairs(vim.split(display, "\n", { plain = true })) do
        lines[#lines + 1] = (index == 1 and (label .. ": ") or "  ") .. vim.fn.strtrans(line)
      end
    end
  end
  for _, record in ipairs(found) do
    field("Trace", record.trace_id)
    field("Origin", record.layer)
    field("Operation", record.operation)
    field("Reason", record.reason)
    field("Message", record.message)
    field("Scope", record.scope)
    if record.resource then
      field("Resource", (record.resource.kind or "unknown") .. " / " .. (record.resource.id or "unknown"))
    end
    field("Retryable", record.retryable)
    field("Failure ID", record.failure_id)
    field("Caused by failure", record.caused_by_failure_id)
    field("Occurred at", record.occurred_at)
    field("PID", record.process.pid)
    field("Exit code", record.process.exit_code)
    field("Signal", record.process.signal)
    for _, key in ipairs(detail_fields) do field(key, record.details[key]) end
    local stderr = record.process.stderr_excerpt
    if stderr and stderr ~= "" then
      lines[#lines + 1] = "stderr" .. (record.process.stderr_truncated and " (tail; truncated):" or ":")
      for _, line in ipairs(vim.split(stderr, "\n", { plain = true })) do
        lines[#lines + 1] = vim.fn.strtrans(line)
      end
    end
    lines[#lines + 1] = ""
  end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].swapfile = false
  vim.bo[buf].undofile = false
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  vim.api.nvim_open_win(buf, true, { split = "below", win = 0 })
  return buf
end

return M
