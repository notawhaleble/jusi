local M = {}
local Parser = {}
Parser.__index = Parser

local function dispatch(self)
  if not self._event.data then
    self._event = {}
    return
  end
  local event = self._event
  event.data = table.concat(event.data, "\n")
  self._event = {}
  self._on_event(event)
end

local function consume_line(self, line)
  if line == "" then
    dispatch(self)
    return
  end
  if line:sub(1, 1) == ":" then
    if self._on_comment then
      local comment = line:sub(2)
      if comment:sub(1, 1) == " " then
        comment = comment:sub(2)
      end
      self._on_comment(comment)
    end
    return
  end
  local separator = line:find(":", 1, true)
  local field
  local value
  if separator then
    field = line:sub(1, separator - 1)
    value = line:sub(separator + 1)
    if value:sub(1, 1) == " " then
      value = value:sub(2)
    end
  else
    field = line
    value = ""
  end
  if field == "data" then
    self._event.data = self._event.data or {}
    table.insert(self._event.data, value)
  elseif field == "id" then
    self._event.id = value
  elseif field == "event" then
    self._event.event = value
  elseif field == "retry" then
    self._event.retry = tonumber(value)
  end
end

function Parser:feed(chunk)
  if not chunk or chunk == "" then
    return
  end
  self._buffer = self._buffer .. chunk
  while true do
    local newline = self._buffer:find("[\r\n]")
    if not newline or (self._buffer:sub(newline, newline) == "\r" and newline == #self._buffer) then
      return
    end
    local line = self._buffer:sub(1, newline - 1)
    local consumed = newline
    if self._buffer:sub(newline, newline + 1) == "\r\n" then
      consumed = newline + 1
    end
    self._buffer = self._buffer:sub(consumed + 1)
    consume_line(self, line)
  end
end

function Parser:finish()
  if self._buffer ~= "" then
    consume_line(self, self._buffer)
    self._buffer = ""
  end
  dispatch(self)
end

function M.new(on_event, on_comment)
  vim.validate("on_event", on_event, "function")
  if on_comment ~= nil then
    vim.validate("on_comment", on_comment, "function")
  end
  return setmetatable({ _buffer = "", _event = {}, _on_event = on_event, _on_comment = on_comment }, Parser)
end

return M
