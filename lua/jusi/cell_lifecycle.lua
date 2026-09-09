-- Runtime cleanup consumes retired identities; the notebook model has no backend dependency.
local M = {}
local Lifecycle = {}
Lifecycle.__index = Lifecycle

-- Only accepted new executions trigger retention cleanup. Outcomes come from
-- controller identities, never status marks or a client factory's return value.
function Lifecycle:cleanup_completed(started)
  if self.closed then return end
  local candidates, protected = {}, {}
  for id, pending in pairs(self.presentation.pending) do
    candidates[id] = pending.execution_id
  end
  for _, client in pairs(self.controller.clients) do
    if client.notebook_id == self.model.notebook_id and client.kernel_id == started.kernel_id then
      if vim.tbl_contains(client.capabilities, "followup") then protected[client.cell_id] = true end
      candidates[client.cell_id] = client.execution_id
    end
  end
  for _, execution in pairs(self.controller.executions) do
    if execution.outcome == "running" then protected[execution.cell_id] = true end
  end
  for id, execution_id in pairs(candidates) do
    local execution = self.controller.executions[execution_id]
    if id ~= started.cell_id and not protected[id] and not self.parked[id] and execution
        and execution.notebook_id == self.model.notebook_id and execution.kernel_id == started.kernel_id
        and (execution.outcome == "succeeded" or execution.outcome == "failed"
          or execution.outcome == "interrupted" or execution.outcome == "cancelled") then
      self:close_cell(id)
    end
  end
end

function Lifecycle:set_park(cell_id, parked)
  local previous = self.parked[cell_id] == true
  self.parked[cell_id] = parked and true or nil
  if previous ~= (parked == true) and self.on_park_changed then self.on_park_changed(cell_id) end
  return parked == true
end

function Lifecycle:toggle_park(cell_id)
  return self:set_park(cell_id, not self.parked[cell_id])
end

function Lifecycle:close_cell(cell_id)
  if self.closed then return end
  if self.inflight[cell_id] then
    if self.retired[cell_id] then self.again[cell_id] = true end
    return
  end
  self:set_park(cell_id, false)
  local controller = self.controller
  local executions, clients = {}, {}
  for id, execution in pairs(controller.executions) do
    if execution.cell_id == cell_id then
      self.presentation:retire_execution(cell_id, id)
      if execution.outcome == "running" and not self.interrupted[id] then table.insert(executions, id) end
    end
  end
  for id, client in pairs(controller.clients) do
    if client.cell_id == cell_id and not self.clients_closed[id] then table.insert(clients, id) end
  end
  self.presentation:close_cell(cell_id)
  self.inflight[cell_id] = true
  local function report(failure)
    if failure and not self.closed and self.on_failure then
      self.on_failure(failure)
    end
  end
  local function close_client(index)
    if self.closed then return end
    local id = clients[index]
    if not id then
      self.inflight[cell_id] = nil
      if self.again[cell_id] then self.again[cell_id] = nil; self:retire(cell_id) end
      return
    end
    if not controller.clients[id] then close_client(index + 1); return end
    controller:close_client(id, function(_, failure)
      if not failure then self.clients_closed[id] = true end
      if failure and not self.closed and self.on_failure then self.on_failure(failure) end
      close_client(index + 1)
    end)
  end
  local function interrupt(index, attempt)
    if self.closed then return end
    local id = executions[index]
    if not id then close_client(1); return end
    local execution = controller.executions[id]
    if not execution or execution.outcome ~= "running" then interrupt(index + 1); return end
    controller:interrupt(id, function(_, failure)
      if self.closed then return end
      local current = controller.executions[id]
      if failure and failure.reason == "conflict" then
        if not current or current.outcome ~= "running" then interrupt(index + 1); return end
        if (attempt or 1) < 3 then
          -- Started may reach the frontend just before the adapter accepts control.
          -- Retry only the captured identity, never whatever execution is now current.
          vim.defer_fn(function() interrupt(index, (attempt or 1) + 1) end, 50)
          return
        end
      end
      if not failure then self.interrupted[id] = true end
      report(failure)
      interrupt(index + 1)
    end)
  end
  interrupt(1)
  return true
end

function Lifecycle:close_client(client_id)
  if self.closed or self.client_inflight[client_id] or self.clients_closed[client_id] then return end
  self.client_inflight[client_id] = true
  vim.schedule(function()
    if self.closed then return end
    if not self.controller.clients[client_id] then self.client_inflight[client_id] = nil; return end
    self.controller:close_client(client_id, function(_, failure)
      self.client_inflight[client_id] = nil
      if not failure then self.clients_closed[client_id] = true end
      if failure and not self.closed and self.on_failure then self.on_failure(failure) end
    end)
  end)
end

function Lifecycle:retire(cell_id)
  if self.closed then return end
  self.retired[cell_id] = true
  if self.scheduled[cell_id] then return end
  self.scheduled[cell_id] = true
  vim.schedule(function()
    self.scheduled[cell_id] = nil
    if not self.closed then self:close_cell(cell_id) end
  end)
end

function Lifecycle:accepts(cell_id)
  if self.closed then return false end
  if self.retired[cell_id] or not self.model:cell_by_id(cell_id) then
    self:retire(cell_id)
    return false
  end
  return true
end

function Lifecycle:reconcile()
  if self.closed then return end
  for _, execution in pairs(self.controller.executions) do
    if execution.notebook_id == self.model.notebook_id then self:accepts(execution.cell_id) end
  end
  for _, client in pairs(self.controller.clients) do
    if client.notebook_id == self.model.notebook_id then self:accepts(client.cell_id) end
  end
  for cell_id in pairs(self.retired) do self:retire(cell_id) end
end

function Lifecycle:detach()
  self.closed = true
end

function M.new(options)
  return setmetatable({ model = options.model, controller = options.controller,
    presentation = options.presentation, on_failure = options.on_failure,
    parked = {}, retired = {}, scheduled = {}, inflight = {}, again = {}, client_inflight = {},
    interrupted = {}, clients_closed = {}, closed = false }, Lifecycle)
end
return M
