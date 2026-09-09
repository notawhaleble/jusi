local M = {}
function M.run()
  local controller = { clients = {}, executions = {} }
  local pending, closed, retired = {}, {}, {}
  local presentation = { pending = pending,
    retire_execution = function(_, _, id) retired[id] = true end,
    close_cell = function(_, id) closed[id] = true; pending[id] = nil end }
  local lifecycle = require('jusi.cell_lifecycle').new({ model = { notebook_id = 'nb' }, controller = controller, presentation = presentation })
  for _, item in ipairs({ { 'done', 'succeeded' }, { 'error', 'failed' }, { 'busy', 'running' },
      { 'followup', 'succeeded' }, { 'parked', 'succeeded' }, { 'other_kernel', 'succeeded' }, { 'interrupted', 'interrupted' } }) do
    local id, outcome = unpack(item)
    controller.executions[id] = { execution_id = id, cell_id = id, notebook_id = 'nb', kernel_id = id == 'other_kernel' and 'elsewhere' or 'kernel', outcome = outcome }
    pending[id] = { execution_id = id }
  end
  controller.clients.followup = { cell_id = 'followup', execution_id = 'followup', notebook_id = 'nb', kernel_id = 'kernel', capabilities = { 'followup' } }
  lifecycle:toggle_park('parked')
  lifecycle:cleanup_completed({ cell_id = 'new', kernel_id = 'kernel' })
  assert(closed.done and closed.error and closed.interrupted)
  assert(retired.done and retired.error)
  assert(not closed.busy and not closed.followup and not closed.parked and not closed.other_kernel)
  lifecycle:toggle_park('parked')
  lifecycle:cleanup_completed({ cell_id = 'new', kernel_id = 'kernel' })
  assert(closed.parked)
  lifecycle:detach()
end
return M
