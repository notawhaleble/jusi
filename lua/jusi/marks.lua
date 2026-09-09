-- Cell status is a projection of observed work, never kernel truth or cell identity.
local M = {}
local Marks = {}
Marks.__index = Marks
local styles = {
  busy = { "*", "JusiCellBusy" }, done = { "✓", "JusiCellDone" },
  error = { "✗", "JusiCellError" }, interrupted = { "!", "JusiCellInterrupted" },
  followup = { ">", "JusiCellFollowup" }, unknown = { "!", "JusiCellUnknown" },
}
local function highlights()
  for name, color in pairs({ Busy = { "#c678dd", 176 }, Done = { "#98c379", 114 },
    Error = { "#e06c75", 168 }, Interrupted = { "#d19a66", 173 },
    Followup = { "#61afef", 75 }, Idle = { "#e5c07b", 180 }, Unknown = { "#7f848e", 102 } }) do
    vim.api.nvim_set_hl(0, "JusiCell" .. name, { fg = color[1], ctermfg = color[2], default = true })
  end
end
local function outcome(value)
  if value == "succeeded" then return "done" end
  if value == "interrupted" or value == "cancelled" then return "interrupted" end
  if value == "failed" then return "error" end
  return "busy"
end

function Marks:render(cell_id)
  if self.closed then return end
  local record = self.records[cell_id]
  local snapshot = self.model:cell_snapshot(cell_id)
  if not snapshot then self:retire(cell_id); return end
  local style = record and styles[record.state] or { "", "JusiCellIdle" }
  local symbol = style[1]
  if self.is_parked and self.is_parked(cell_id) then symbol = symbol == "" and "~" or (symbol .. " ~") end
  local cell_mode = vim.b[self.model.buf].jusi_cell_mode_active == true
  local parser = require("jusi.notebook.parser")
  if snapshot.close_row then
    self.closers[cell_id] = vim.api.nvim_buf_set_extmark(self.model.buf, self.namespace, snapshot.close_row, 0, {
      id = self.closers[cell_id], end_col = #parser.lines.close,
      virt_text = cell_mode and { { "╚══", style[2] } } or {}, virt_text_pos = "overlay",
      right_gravity = true, end_right_gravity = false, invalidate = true, undo_restore = false,
      hl_group = style[2], priority = 120,
    })
  elseif self.closers[cell_id] then
    vim.api.nvim_buf_del_extmark(self.model.buf, self.namespace, self.closers[cell_id])
    self.closers[cell_id] = nil
  end
  if not record then
    self.idle[cell_id] = vim.api.nvim_buf_set_extmark(self.model.buf, self.namespace, snapshot.open_row, 0, {
      id = self.idle[cell_id], end_col = #require("jusi.notebook.parser").lines.open,
      virt_text = cell_mode and { { "╔══" .. (symbol ~= "" and (" " .. symbol) or ""), style[2] } }
        or (symbol ~= "" and { { symbol, style[2] } } or {}),
      virt_text_pos = cell_mode and "overlay" or "eol",
      right_gravity = true, end_right_gravity = false, invalidate = true, undo_restore = false,
      hl_group = style[2], priority = 120,
    })
    return
  end
  if self.idle[cell_id] then
    vim.api.nvim_buf_del_extmark(self.model.buf, self.namespace, self.idle[cell_id])
    self.idle[cell_id] = nil
  end
  record.mark = vim.api.nvim_buf_set_extmark(self.model.buf, self.namespace, snapshot.open_row, 0, {
    id = record.mark, end_col = #require("jusi.notebook.parser").lines.open,
    right_gravity = true, end_right_gravity = false, invalidate = true, undo_restore = false,
    hl_group = style[2],
    virt_text = { { cell_mode and ("╔══ " .. symbol) or symbol, style[2] } },
    virt_text_pos = cell_mode and "overlay" or "eol", priority = 120,
  })
end
function Marks:retire(cell_id)
  if self.closers[cell_id] and vim.api.nvim_buf_is_valid(self.model.buf) then
    vim.api.nvim_buf_del_extmark(self.model.buf, self.namespace, self.closers[cell_id])
  end
  self.closers[cell_id] = nil
  if self.idle[cell_id] and vim.api.nvim_buf_is_valid(self.model.buf) then
    vim.api.nvim_buf_del_extmark(self.model.buf, self.namespace, self.idle[cell_id])
  end
  self.idle[cell_id] = nil
  local record = self.records[cell_id]
  if record and record.mark and vim.api.nvim_buf_is_valid(self.model.buf) then
    vim.api.nvim_buf_del_extmark(self.model.buf, self.namespace, record.mark)
  end
  self.records[cell_id] = nil
end
function Marks:execution(execution)
  if self.closed or execution.notebook_id ~= self.model.notebook_id
    or not self.model:cell_by_id(execution.cell_id) then return end
  local id = execution.cell_id
  local record = self.records[id] or {}
  if execution.outcome ~= "running" and record.execution_id ~= execution.execution_id then return end
  record.execution_id = execution.execution_id
  record.client_id = type(execution.client_id) == "string" and execution.client_id or nil
  record.operation_id = nil
  record.outcome = execution.outcome
  record.state = outcome(execution.outcome)
  local client = record.client_id and self.controller.clients[record.client_id]
  if record.state == "done" and client and vim.tbl_contains(client.capabilities, "followup") then
    record.state = "followup"
  end
  self.records[id] = record
  self:render(id)
end
function Marks:client(client)
  if not client or client.notebook_id ~= self.model.notebook_id or not self.model:cell_by_id(client.cell_id) then return end
  local record = self.records[client.cell_id] or { execution_id = client.execution_id, outcome = "succeeded" }
  if record.execution_id ~= client.execution_id then return end
  record.client_id = client.client_id
  if record.outcome ~= "running" then
    record.state = vim.tbl_contains(client.capabilities, "followup") and "followup" or "done"
  end
  self.records[client.cell_id] = record
  self:render(client.cell_id)
end
function Marks:event(event)
  if self.closed then return end
  local p = event.payload
  if event.kind == "execution.started" or event.kind == "execution.completed" then
    self:execution(p)
  elseif event.kind == "client.created" then self:client(p)
  elseif event.kind == "client.closed" then
    for id, record in pairs(self.records) do
      if record.client_id == p.client_id then
        record.client_id, record.operation_id = nil, nil
        record.state = p.reason == "fatal_failure" and "error" or outcome(record.outcome)
        self:render(id)
        break
      end
    end
  elseif event.operation == "followup" and event.resource.kind == "client" then
    local client = self.controller.clients[event.resource.id]
    local record = client and self.records[client.cell_id]
    if not record or record.client_id ~= client.client_id then return end
    if event.kind == "operation.started" then
      record.operation_id, record.state = p.operation_id, "busy"
    elseif event.kind == "operation.completed" and record.operation_id == p.operation_id then
      record.operation_id = nil
      record.state = p.outcome == "succeeded" and "followup" or outcome(p.outcome)
    else return end
    self:render(client.cell_id)
  end
end
function Marks:resync(snapshot)
  if snapshot.reason == "supervisor_replaced" then
    self.records, self.idle, self.closers = {}, {}, {}
    vim.api.nvim_buf_clear_namespace(self.model.buf, self.namespace, 0, -1)
    for _, cell in ipairs(self.model:ordered_cells()) do self:render(cell.id) end
  elseif snapshot.reason == "cursor_expired" then
    -- Health cannot recover outcomes which fell outside the event replay window.
    for id, record in pairs(self.records) do
      record.state, record.operation_id = "unknown", nil
      self:render(id)
    end
  end
  for _, execution in pairs(self.controller.executions) do self:execution(execution) end
  for _, client in pairs(self.controller.clients) do self:client(client) end
  for _, operation in pairs(self.controller.client_operations or {}) do
    local client = self.controller.clients[operation.client_id]
    local record = client and self.records[client.cell_id]
    if record and operation.kind == "followup" then
      record.operation_id, record.state = operation.operation_id, "busy"
      self:render(client.cell_id)
    end
  end
end
function Marks:close()
  if self.closed then return end
  self.closed = true
  vim.api.nvim_del_augroup_by_id(self.group)
  if vim.api.nvim_buf_is_valid(self.model.buf) then
    vim.api.nvim_buf_clear_namespace(self.model.buf, self.namespace, 0, -1)
  end
  self.records, self.idle, self.closers = {}, {}, {}
  if self.model.on_cells_changed == self.on_cells_changed then self.model.on_cells_changed = nil end
end
function M.new(model, controller)
  highlights()
  local marks = setmetatable({ model = model, controller = controller, records = {}, idle = {}, closers = {},
    namespace = vim.api.nvim_create_namespace("jusi_cell_status_" .. model.notebook_id) }, Marks)
  marks.group = vim.api.nvim_create_augroup("jusi_cell_status_" .. model.notebook_id, { clear = true })
  vim.api.nvim_create_autocmd("ColorScheme", { group = marks.group, callback = highlights })
  vim.api.nvim_create_autocmd("User", { group = marks.group, pattern = "JusiCellModeChanged", callback = function(event)
    if event.data and event.data.buf == model.buf then
      for _, cell in ipairs(model:ordered_cells()) do marks:render(cell.id) end
    end
  end })
  marks.on_cells_changed = function(ids)
    for _, id in ipairs(ids) do marks:render(id) end
  end
  model.on_cells_changed = marks.on_cells_changed
  for _, cell in ipairs(model:ordered_cells()) do marks:render(cell.id) end
  return marks
end
return M
