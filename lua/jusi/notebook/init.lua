local parser = require("jusi.notebook.parser")

local M = {}
local Notebook = {}
Notebook.__index = Notebook

local notebook_counter = 0

function M.new_notebook_id()
  notebook_counter = notebook_counter + 1
  return string.format("nb_%x_%x", vim.uv.hrtime(), notebook_counter)
end

local function mark_options(end_col)
  return {
    invalidate = true,
    undo_restore = false,
    right_gravity = true,
    end_col = end_col,
    end_right_gravity = false,
    strict = false,
  }
end

local function percentile(values, fraction)
  if #values == 0 then
    return 0
  end
  local copy = vim.deepcopy(values)
  table.sort(copy)
  return copy[math.max(1, math.ceil(#copy * fraction))]
end

local function mark_position(buf, namespace, id)
  if not id then
    return nil
  end
  local position = vim.api.nvim_buf_get_extmark_by_id(buf, namespace, id, {})
  if #position == 0 then
    return nil
  end
  return position[1]
end

function Notebook:_new_cell_id()
  self._next_cell_number = self._next_cell_number + 1
  return string.format("%s_cell_%x", self.notebook_id, self._next_cell_number)
end

function Notebook:_set_mark(namespace, row, end_col)
  return vim.api.nvim_buf_set_extmark(self.buf, namespace, row, 0, mark_options(end_col))
end

function Notebook:_delete_mark(namespace, id)
  if id then
    pcall(vim.api.nvim_buf_del_extmark, self.buf, namespace, id)
  end
end

function Notebook:_delete_cell_marks(cell, preserve_opener)
  if not preserve_opener then
    self:_delete_mark(self._open_namespace, cell.open_extmark_id)
    self._cell_by_open_extmark[cell.open_extmark_id] = nil
  end
  for _, marker in ipairs(cell.markers) do
    self:_delete_mark(self._structure_namespace, marker.extmark_id)
  end
  cell.markers = {}
end

function Notebook:_update_header(cell, open_row)
  local line = vim.api.nvim_buf_get_lines(self.buf, open_row + 1, open_row + 2, false)[1] or ""
  local header = line:match("^%s*%%%%") and line or ""
  if cell.header ~= header then
    cell.header, cell.header_revision = header, (cell.header_revision or 0) + 1
  end
end

function Notebook:_materialize_cell(raw, reusable)
  local cell = reusable
  if cell then
    self:_delete_cell_marks(cell, true)
  else
    local open_extmark_id = self:_set_mark(self._open_namespace, raw.open_row, #parser.lines.open)
    cell = {
      id = self:_new_cell_id(),
      open_extmark_id = open_extmark_id,
      text_revision = 0,
      markers = {},
    }
    self._cell_by_open_extmark[open_extmark_id] = cell
    self._cell_by_id[cell.id] = cell
  end

  self:_update_header(cell, raw.open_row)
  cell.valid = raw.valid
  cell.markers = {}
  if raw.history_row then
    table.insert(cell.markers, {
      kind = "history",
      extmark_id = self:_set_mark(self._structure_namespace, raw.history_row, #parser.lines.history),
    })
  end
  for _, row in ipairs(raw.history_separator_rows) do
    table.insert(cell.markers, {
      kind = "history_separator",
      extmark_id = self:_set_mark(self._structure_namespace, row, #parser.lines.history_separator),
    })
  end
  if raw.close_row then
    table.insert(cell.markers, {
      kind = "close",
      extmark_id = self:_set_mark(self._structure_namespace, raw.close_row, #parser.lines.close),
    })
  end
  return cell
end

function Notebook:_add_diagnostic(raw, cells_by_parse_index)
  local row_count = vim.api.nvim_buf_line_count(self.buf)
  local row = math.min(raw.row, math.max(0, row_count - 1))
  local line = vim.api.nvim_buf_get_lines(self.buf, row, row + 1, false)[1] or ""
  local extmark_id = self:_set_mark(self._diagnostic_namespace, row, math.max(1, #line))
  self._diagnostic_by_extmark[extmark_id] = {
    code = raw.code,
    message = raw.message,
    cell_id = raw.cell_index and cells_by_parse_index[raw.cell_index].id or nil,
  }
end

function Notebook:_clear_diagnostics(start_row, end_row)
  local marks = vim.api.nvim_buf_get_extmarks(
    self.buf,
    self._diagnostic_namespace,
    { start_row, 0 },
    { end_row, 0 },
    {}
  )
  for _, mark in ipairs(marks) do
    self._diagnostic_by_extmark[mark[1]] = nil
    self:_delete_mark(self._diagnostic_namespace, mark[1])
  end
end

function Notebook:_notify_retired(ids)
  if #ids == 0 or not self.on_cells_retired then return end
  -- Notifications never run inside an edit callback or a model query.
  vim.schedule(function()
    if not self._detached and self.on_cells_retired then self.on_cells_retired(ids) end
  end)
end

function Notebook:_notify_cells_changed(ids)
  if #ids == 0 or not self.on_cells_changed then return end
  vim.schedule(function()
    if not self._detached and self.on_cells_changed then self.on_cells_changed(ids) end
  end)
end

function Notebook:_full_parse()
  local retired_ids = {}
  local previous = self._head
  while previous do table.insert(retired_ids, previous.id); previous = previous.next end
  self._dirty = nil
  vim.api.nvim_buf_clear_namespace(self.buf, self._open_namespace, 0, -1)
  vim.api.nvim_buf_clear_namespace(self.buf, self._structure_namespace, 0, -1)
  vim.api.nvim_buf_clear_namespace(self.buf, self._diagnostic_namespace, 0, -1)
  self._head = nil
  self._tail = nil
  self._cell_by_open_extmark = {}
  self._cell_by_id = {}
  self._diagnostic_by_extmark = {}

  local lines = vim.api.nvim_buf_get_lines(self.buf, 0, -1, false)
  local parsed = parser.parse(lines, 0)
  local parsed_cells = {}
  for _, raw in ipairs(parsed.cells) do
    local cell = self:_materialize_cell(raw, nil)
    cell.prev = self._tail
    cell.next = nil
    if self._tail then
      self._tail.next = cell
    else
      self._head = cell
    end
    self._tail = cell
    table.insert(parsed_cells, cell)
  end
  for _, raw in ipairs(parsed.diagnostics) do
    self:_add_diagnostic(raw, parsed_cells)
  end

  self.metrics.full_parse_count = self.metrics.full_parse_count + 1
  self.metrics.full_parse_lines = self.metrics.full_parse_lines + #lines
  self.last_change = { kind = "full_parse", scanned_line_count = #lines, affected_cell_ids = {} }
  self:_notify_retired(retired_ids)
  local ids = {}
  for _, cell in ipairs(parsed_cells) do table.insert(ids, cell.id) end
  self:_notify_cells_changed(ids)
end

function Notebook:_nearest_open_before(row)
  local marks = vim.api.nvim_buf_get_extmarks(self.buf, self._open_namespace, { row, -1 }, 0, { limit = 1 })
  if #marks == 0 then
    return nil, nil
  end
  return self._cell_by_open_extmark[marks[1][1]], marks[1][2]
end

function Notebook:_next_open_at_or_after(row)
  local marks = vim.api.nvim_buf_get_extmarks(self.buf, self._open_namespace, { row, 0 }, -1, { limit = 1 })
  if #marks == 0 then
    return nil, nil
  end
  return self._cell_by_open_extmark[marks[1][1]], marks[1][2]
end

function Notebook:_marker_rows(cell)
  local rows = { history_separators = {} }
  for _, marker in ipairs(cell.markers) do
    local row = mark_position(self.buf, self._structure_namespace, marker.extmark_id)
    if row == nil then
      return nil
    end
    local line = vim.api.nvim_buf_get_lines(self.buf, row, row + 1, false)[1]
    if line ~= parser.lines[marker.kind] then
      return nil
    end
    if marker.kind == "history_separator" then
      table.insert(rows.history_separators, row)
    else
      rows[marker.kind] = row
    end
  end
  table.sort(rows.history_separators)
  return rows
end

function Notebook:_is_local_text_edit(first_line, new_last_line)
  local changed_lines = vim.api.nvim_buf_get_lines(self.buf, first_line, new_last_line, false)
  for _, line in ipairs(changed_lines) do
    if parser.line_kind(line) then
      return false
    end
  end

  local cell, open_row = self:_nearest_open_before(first_line)
  if not cell or not cell.valid or first_line <= open_row then
    return false
  end
  local marker_rows = self:_marker_rows(cell)
  if not marker_rows or not marker_rows.close then
    return false
  end
  if first_line > marker_rows.close or new_last_line > marker_rows.close then
    return false
  end
  return true, cell, open_row, marker_rows.history and first_line > marker_rows.history
end

function Notebook:_replace_region(start_row, end_row)
  -- Deleted openers may have no extmark left at all. Start from the model
  -- successor of the left boundary, not the first surviving opener inside
  -- the region, so removed cells are included in retirement.
  local left = start_row > 0 and self:_nearest_open_before(start_row - 1) or nil
  local first_old
  if left then first_old = left.next else first_old = self._head end
  local end_cell = self:_next_open_at_or_after(end_row)

  local reusable_by_row = {}
  local old_cells = {}
  local old_cell = first_old
  while old_cell and old_cell ~= end_cell do
    table.insert(old_cells, old_cell)
    local row = mark_position(self.buf, self._open_namespace, old_cell.open_extmark_id)
    local anchor = vim.api.nvim_buf_get_extmark_by_id(self.buf, self._open_namespace, old_cell.open_extmark_id, { details = true })
    if row and row >= start_row and row < end_row and anchor[3] and not anchor[3].invalid then
      reusable_by_row[row] = old_cell
    end
    old_cell = old_cell.next
  end

  local lines = vim.api.nvim_buf_get_lines(self.buf, start_row, end_row, false)
  local parsed = parser.parse(lines, start_row)
  local replacement = {}
  local reused = {}
  for _, raw in ipairs(parsed.cells) do
    local reusable = reusable_by_row[raw.open_row]
    if reusable then
      reused[reusable.id] = true
    end
    table.insert(replacement, self:_materialize_cell(raw, reusable))
  end
  local retired_ids = {}
  for _, cell in ipairs(old_cells) do
    if not reused[cell.id] then
      table.insert(retired_ids, cell.id)
      self:_delete_cell_marks(cell, false)
      self._cell_by_id[cell.id] = nil
      cell.prev = nil
      cell.next = nil
    end
  end

  local previous = left
  for _, cell in ipairs(replacement) do
    cell.prev = previous
    if previous then
      previous.next = cell
    else
      self._head = cell
    end
    previous = cell
  end
  if previous then
    previous.next = end_cell
  elseif left then
    left.next = end_cell
  else
    self._head = end_cell
  end
  if end_cell then
    end_cell.prev = previous or left
  else
    self._tail = previous or left
  end

  self:_clear_diagnostics(start_row, end_row)
  for _, raw in ipairs(parsed.diagnostics) do
    self:_add_diagnostic(raw, replacement)
  end
  self:_notify_retired(retired_ids)
  return replacement, #lines, math.max(#old_cells, #replacement)
end

function Notebook:_structural_region(first_line, new_last_line)
  local previous, open_row = nil, nil
  if first_line > 0 then
    previous, open_row = self:_nearest_open_before(first_line - 1)
  end
  local start_row = first_line
  if previous then
    local marker_rows = self:_marker_rows(previous)
    if not marker_rows or not marker_rows.close or marker_rows.close >= first_line then
      start_row = open_row
    end
  end

  local search_from = math.max(new_last_line, start_row + 1)
  local _, next_open_row = self:_next_open_at_or_after(search_from)
  local end_row = next_open_row or vim.api.nvim_buf_line_count(self.buf)
  return start_row, end_row
end

function Notebook:_notify_text_changed(id, history)
  if not self.on_text_changed then return end
  if history then self._pending_history_changes = self._pending_history_changes or {}; self._pending_history_changes[id] = true end
  if self._pending_text_changes then self._pending_text_changes[id] = true; return end
  self._pending_text_changes = { [id] = true }
  vim.schedule(function()
    local changed, history_changed = self._pending_text_changes, self._pending_history_changes
    self._pending_text_changes, self._pending_history_changes = nil, nil
    if not self._detached and self.on_text_changed then
      self.on_text_changed(vim.tbl_keys(changed), history_changed and vim.tbl_keys(history_changed) or {})
    end
  end)
end

function Notebook:_on_lines(first_line, old_last_line, new_last_line)
  if self._detached then return end
  if not self._dirty then
    local is_local, cell, open_row, history = self:_is_local_text_edit(first_line, new_last_line)
    if is_local then
      if first_line <= open_row + 1 then self:_update_header(cell, open_row) end
      cell.text_revision = cell.text_revision + 1
      self.metrics.body_edit_count = self.metrics.body_edit_count + 1
      self.last_change = { kind = "body", scanned_line_count = new_last_line - first_line, affected_cell_ids = { cell.id } }
      self:_notify_text_changed(cell.id, history)
      return
    end
  end

  -- Undo applies extmark adjustments after on_lines. Creating or replacing
  -- anchors inside that callback lets undo move the new anchors a second time.
  -- Keep only a local dirty span here; reconcile after the edit has settled.
  local dirty = self._dirty
  if dirty then
    local delta = new_last_line - old_last_line
    local function moved(row)
      if row >= old_last_line then return row + delta end
      if row >= first_line then return new_last_line end
      return row
    end
    dirty.first = math.min(first_line, moved(dirty.first))
    dirty.last = math.max(new_last_line, moved(dirty.last))
  else
    self._dirty = { first = first_line, last = new_last_line }
  end
  if not self._flush_scheduled then
    self._flush_scheduled = true
    vim.schedule(function()
      self._flush_scheduled = false
      self:flush()
    end)
  end
end

function Notebook:flush()
  if self._detached or not self._dirty then return end
  local dirty = self._dirty
  self._dirty = nil
  local first_line, new_last_line = dirty.first, dirty.last
  local start_row, end_row = self:_structural_region(first_line, new_last_line)
  local affected, scanned, reconciled_cells = self:_replace_region(start_row, end_row)
  local ids = {}
  for _, affected_cell in ipairs(affected) do
    table.insert(ids, affected_cell.id)
  end
  self.metrics.structural_reconcile_count = self.metrics.structural_reconcile_count + 1
  self.metrics.structural_scanned_lines = self.metrics.structural_scanned_lines + scanned
  self.metrics.max_structural_scan = math.max(self.metrics.max_structural_scan, scanned)
  self.metrics.max_structural_cells = math.max(self.metrics.max_structural_cells, reconciled_cells)
  self.last_change = { kind = "structural", scanned_line_count = scanned, affected_cell_ids = ids }
  self:_notify_cells_changed(ids)
end

function Notebook:cell_snapshot(cell_or_id)
  self:flush()
  local cell = type(cell_or_id) == "table" and cell_or_id or self:cell_by_id(cell_or_id)
  if not cell then
    return nil
  end
  local open_row = mark_position(self.buf, self._open_namespace, cell.open_extmark_id)
  local rows = self:_marker_rows(cell)
  if not open_row or not rows then
    return nil
  end
  if vim.api.nvim_buf_get_lines(self.buf, open_row, open_row + 1, false)[1] ~= parser.lines.open then
    return nil
  end
  local end_row = rows.close and (rows.close + 1)
    or (cell.next and mark_position(self.buf, self._open_namespace, cell.next.open_extmark_id))
    or vim.api.nvim_buf_line_count(self.buf)
  local body_end = rows.history or rows.close or end_row
  local history_entries = {}
  if rows.history and rows.close then
    local entry_start = rows.history + 1
    for _, separator_row in ipairs(rows.history_separators) do
      table.insert(history_entries, { start_row = entry_start, end_row = separator_row })
      entry_start = separator_row + 1
    end
    table.insert(history_entries, { start_row = entry_start, end_row = rows.close })
  end
  return {
    id = cell.id,
    valid = cell.valid,
    text_revision = cell.text_revision,
    open_row = open_row,
    body_start_row = open_row + 1,
    body_end_row = body_end,
    history_row = rows.history,
    history_entries = history_entries,
    close_row = rows.close,
    end_row = end_row,
    open_extmark_id = cell.open_extmark_id,
  }
end

function Notebook:cell_by_id(cell_id)
  self:flush()
  return self._cell_by_id[cell_id]
end

function Notebook:cell_at_row(row)
  self:flush()
  vim.validate("row", row, "number")
  local cell = self:_nearest_open_before(row)
  if not cell then
    return nil
  end
  local snapshot = self:cell_snapshot(cell)
  if snapshot and row >= snapshot.open_row and row < snapshot.end_row then
    return cell
  end
  return nil
end

function Notebook:ordered_cells()
  self:flush()
  local result = {}
  local cell = self._head
  while cell do
    table.insert(result, cell)
    cell = cell.next
  end
  return result
end

function Notebook:body(cell_or_id)
  local snapshot = self:cell_snapshot(cell_or_id)
  if not snapshot or not snapshot.valid then
    return nil, "cell is structurally invalid"
  end
  return vim.api.nvim_buf_get_lines(self.buf, snapshot.body_start_row, snapshot.body_end_row, false)
end

function Notebook:history(cell_or_id)
  local snapshot = self:cell_snapshot(cell_or_id)
  if not snapshot or not snapshot.valid then
    return nil, "cell is structurally invalid"
  end
  local entries = {}
  for _, entry in ipairs(snapshot.history_entries) do
    table.insert(entries, vim.api.nvim_buf_get_lines(self.buf, entry.start_row, entry.end_row, false))
  end
  return entries
end

function Notebook:diagnostics()
  self:flush()
  local result = {}
  local marks = vim.api.nvim_buf_get_extmarks(self.buf, self._diagnostic_namespace, 0, -1, {})
  for _, mark in ipairs(marks) do
    local item = self._diagnostic_by_extmark[mark[1]]
    if item then
      table.insert(result, {
        code = item.code,
        message = item.message,
        cell_id = item.cell_id,
        row = mark[2],
      })
    end
  end
  return result
end

function Notebook:detach()
  if self._detached then
    return
  end
  self._detached = true
  pcall(vim.api.nvim_buf_detach, self.buf)
  vim.api.nvim_buf_clear_namespace(self.buf, self._open_namespace, 0, -1)
  vim.api.nvim_buf_clear_namespace(self.buf, self._structure_namespace, 0, -1)
  vim.api.nvim_buf_clear_namespace(self.buf, self._diagnostic_namespace, 0, -1)
end

function M.attach(buf, options)
  vim.validate("buf", buf, "number")
  local opts = options or {}
  local notebook_id = opts.notebook_id or M.new_notebook_id()
  local self = setmetatable({
    buf = buf,
    notebook_id = notebook_id,
    metrics = {
      full_parse_count = 0,
      full_parse_lines = 0,
      body_edit_count = 0,
      structural_reconcile_count = 0,
      structural_scanned_lines = 0,
      max_structural_scan = 0,
      max_structural_cells = 0,
    },
    _next_cell_number = 0,
    _cell_by_open_extmark = {},
    _cell_by_id = {},
    _diagnostic_by_extmark = {},
    _open_namespace = vim.api.nvim_create_namespace("jusi.notebook.open." .. notebook_id),
    _structure_namespace = vim.api.nvim_create_namespace("jusi.notebook.structure." .. notebook_id),
    _diagnostic_namespace = vim.api.nvim_create_namespace("jusi.notebook.diagnostic." .. notebook_id),
    _detached = false,
  }, Notebook)
  self:_full_parse()

  if opts.attach ~= false then
    vim.api.nvim_buf_attach(buf, false, {
      on_lines = function(_, _, _, first_line, old_last_line, new_last_line)
        self:_on_lines(first_line, old_last_line, new_last_line)
      end,
      on_reload = function()
        self:_full_parse()
      end,
      on_detach = function()
        self._detached = true
      end,
    })
  end
  return self
end

M.Notebook = Notebook
M.parser = parser
M.percentile = percentile

return M
