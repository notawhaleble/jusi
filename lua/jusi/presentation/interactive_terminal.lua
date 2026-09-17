local M = {}
local presentation_window = require("jusi.presentation.window")
local InteractiveTerminals = {}
InteractiveTerminals.__index = InteractiveTerminals
local next_group = 0

local function window_bottom(win)
  local ok, bottom = pcall(vim.api.nvim_win_call, win, function()
    return vim.fn.line("w$")
  end)
  return ok and bottom or 0
end

local function scroll_to_bottom(record, win)
  if not vim.api.nvim_win_is_valid(win) or vim.api.nvim_win_get_buf(win) ~= record.buf then return end
  record.following[win] = true
  pcall(vim.api.nvim_win_call, win, function()
    vim.cmd("silent! normal! Gzb")
  end)
end

local function default_launch(options)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.api.nvim_buf_set_name(buf, "jusi://terminal/" .. options.surface.surface_id)
  vim.b[buf].jusi_role = "interactive_terminal"
  vim.b[buf].jusi_notebook_id = options.notebook_id
  vim.b[buf].jusi_cell_id = options.client.cell_id
  vim.b[buf].jusi_client_id = options.surface.client_id
  vim.b[buf].jusi_surface_id = options.surface.surface_id
  require("jusi.focus").attach(buf)
  require("jusi.output_ids").attach(buf)
  local window = presentation_window.show(buf, {
    anchor_buf = options.notebook_buf,
    height = options.height,
    enter = false,
  })
  local job_id
  vim.api.nvim_buf_call(buf, function()
    job_id = vim.fn.jobstart(options.command, {
      term = true,
      on_exit = options.on_exit,
    })
  end)
  if type(job_id) ~= "number" or job_id <= 0 then
    presentation_window.close_for_buffer(buf)
    pcall(vim.api.nvim_buf_delete, buf, { force = true })
    error("could not start terminal bridge: " .. tostring(job_id))
  end
  return { buf = buf, window = window, job_id = job_id }
end

function InteractiveTerminals:_failure(reason, message, surface)
  if self.on_failure then
    self.on_failure({
      trace_id = "",
      layer = "frontend_transport",
      operation = "attach_terminal_surface",
      reason = reason,
      message = message,
      retryable = true,
      scope = "transport",
      resource = { kind = "surface", id = surface.surface_id },
    })
  end
end

function InteractiveTerminals:open(surface, client)
  if self.surfaces[surface.surface_id] then
    return self.surfaces[surface.surface_id]
  end
  if surface.kind ~= "terminal" or not client then
    self:_failure("protocol_violation", "terminal surface has no authoritative client", surface)
    return nil
  end
  local command = vim.deepcopy(self.command)
  table.insert(command, self.base_url)
  table.insert(command, surface.surface_id)
  if self.editor_id then vim.list_extend(command, { "--editor-id", self.editor_id }) end
  local record = { surface = surface, client = client, closed = false }
  local ok, launched = pcall(self.launch, {
    command = command,
    height = self.height,
    notebook_buf = self.notebook_buf,
    notebook_id = self.notebook_id,
    surface = surface,
    client = client,
    on_exit = function(_, exit_code)
      if not record.closed and exit_code ~= 0 then
        self:_failure("channel_closed", "terminal bridge exited with code " .. tostring(exit_code), surface)
      end
    end,
  })
  if not ok then
    self:_failure("channel_closed", tostring(launched), surface)
    return nil
  end
  record.buf = launched.buf
  record.window = launched.window
  record.job_id = launched.job_id
  record.following = {}
  record.line_count = vim.api.nvim_buf_line_count(record.buf)
  vim.api.nvim_create_autocmd("TextChangedT", {
    group = self.group,
    buffer = record.buf,
    callback = function() self:_follow_output(record) end,
  })
  self.surfaces[surface.surface_id] = record
  for _, win in ipairs(vim.fn.win_findbuf(record.buf)) do scroll_to_bottom(record, win) end
  return record
end

function InteractiveTerminals:_follow_output(record)
  if record.closed or not record.buf or not vim.api.nvim_buf_is_valid(record.buf) then return end
  local previous_line_count = record.line_count or vim.api.nvim_buf_line_count(record.buf)
  local current_line_count = vim.api.nvim_buf_line_count(record.buf)
  record.line_count = current_line_count
  local previous_bottom = math.min(previous_line_count, current_line_count)
  local visible = {}
  for _, win in ipairs(vim.fn.win_findbuf(record.buf)) do
    visible[win] = true
    if record.following[win] ~= false then
      if window_bottom(win) < previous_bottom then
        record.following[win] = false
      else
        scroll_to_bottom(record, win)
      end
    end
  end
  for win, _ in pairs(record.following) do
    if not visible[win] then record.following[win] = nil end
  end
end

function InteractiveTerminals:prepare_followup(cell_id)
  for _, record in pairs(self.surfaces) do
    if record.client.cell_id == cell_id and record.buf and vim.api.nvim_buf_is_valid(record.buf) then
      record.line_count = vim.api.nvim_buf_line_count(record.buf)
      for _, win in ipairs(vim.fn.win_findbuf(record.buf)) do scroll_to_bottom(record, win) end
    end
  end
end

function InteractiveTerminals:close_surface(surface_id)
  local record = self.surfaces[surface_id]
  if not record then
    return false
  end
  self.surfaces[surface_id] = nil
  record.closed = true
  if record.job_id then
    pcall(vim.fn.jobstop, record.job_id)
  end
  if record.buf and vim.api.nvim_buf_is_valid(record.buf) then
    presentation_window.close_for_buffer(record.buf)
    pcall(vim.api.nvim_buf_delete, record.buf, { force = true })
  end
  return true
end

function InteractiveTerminals:reconcile(surfaces, clients)
  for surface_id, _ in pairs(self.surfaces) do
    if not surfaces[surface_id] then
      self:close_surface(surface_id)
    end
  end
  for surface_id, surface in pairs(surfaces) do
    if not self.surfaces[surface_id] then
      self:open(surface, clients[surface.client_id])
    end
  end
end

function InteractiveTerminals:buffer_for_cell(cell_id)
  for _, record in pairs(self.surfaces) do
    if record.client.cell_id == cell_id and record.buf and vim.api.nvim_buf_is_valid(record.buf) then
      return record.buf
    end
  end
  return nil
end

function InteractiveTerminals:close()
  for _, surface_id in ipairs(vim.tbl_keys(self.surfaces)) do
    self:close_surface(surface_id)
  end
  pcall(vim.api.nvim_del_augroup_by_id, self.group)
end

function M.new(options)
  vim.validate("base_url", options.base_url, "string")
  vim.validate("command", options.command, "table")
  assert(#options.command > 0, "terminal bridge command must not be empty")
  next_group = next_group + 1
  return setmetatable({
    base_url = options.base_url:gsub("/+$", ""),
    command = vim.deepcopy(options.command),
    height = options.height or 12,
    notebook_buf = options.notebook_buf,
    notebook_id = options.notebook_id,
    on_failure = options.on_failure,
    launch = options.launch or default_launch,
    surfaces = {},
    group = vim.api.nvim_create_augroup("jusi-interactive-terminal-" .. next_group, { clear = true }),
  }, InteractiveTerminals)
end

M.InteractiveTerminals = InteractiveTerminals

return M
