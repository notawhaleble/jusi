local notebook = require("jusi.notebook")
local parser = require("jusi.notebook.parser")

local M = {}

local function milliseconds(started)
  return (vim.uv.hrtime() - started) / 1e6
end

local function reference_lines()
  local lines = {}
  for index = 1, 1000 do
    vim.list_extend(lines, {
      "╭──",
      "value_" .. index .. " = " .. index,
      "value_" .. index .. " + 1",
      "ordinary text",
      "%%magic-looking text",
      "╞══",
      "previous value",
      "├┄┄",
      "older value",
      "╰──",
    })
  end
  return lines
end

local function measure(iterations, callback)
  local values = {}
  for index = 1, iterations do
    local started = vim.uv.hrtime()
    callback(index)
    table.insert(values, milliseconds(started))
  end
  return values
end

local function assert_budget(name, actual, budget)
  assert(actual <= budget, string.format("%s %.3f ms exceeds %.3f ms budget", name, actual, budget))
end

function M.run()
  local lines = reference_lines()
  assert(#lines == 10000)
  local version = vim.version()
  local uname = vim.uv.os_uname()

  local parse_times = measure(25, function()
    local result = parser.parse(lines)
    assert(#result.cells == 1000 and #result.diagnostics == 0)
  end)

  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  local attach_started = vim.uv.hrtime()
  local model = notebook.attach(buf)
  local attach_ms = milliseconds(attach_started)
  assert(#model:ordered_cells() == 1000)

  local edit_rows = { 1, 4991, 9991 }
  local body_times = measure(300, function(index)
    local row = edit_rows[((index - 1) % #edit_rows) + 1]
    vim.api.nvim_buf_set_lines(buf, row, row + 1, false, { "edited value " .. index })
    assert(model.last_change.kind == "body")
  end)

  local target = model:cell_snapshot(model:ordered_cells()[500])
  local split_row = target.open_row + 3
  local structural_times = measure(100, function(index)
    if index % 2 == 1 then
      vim.api.nvim_buf_set_lines(buf, split_row, split_row, false, { "╰──", "╭──" })
    else
      vim.api.nvim_buf_set_lines(buf, split_row, split_row + 2, false, {})
    end
    model:flush()
    assert(model.last_change.kind == "structural")
  end)

  local metrics = {
    environment = {
      sysname = uname.sysname,
      release = uname.release,
      machine = uname.machine,
      nvim = string.format("%d.%d.%d", version.major, version.minor, version.patch),
      lua = jit and jit.version or _VERSION,
      mode = "headless; parser samples warm after module load",
    },
    reference_lines = #lines,
    reference_cells = 1000,
    initial_parse_p95_ms = notebook.percentile(parse_times, 0.95),
    initial_model_attach_ms = attach_ms,
    body_edit_p95_ms = notebook.percentile(body_times, 0.95),
    body_edit_p99_ms = notebook.percentile(body_times, 0.99),
    structural_edit_p95_ms = notebook.percentile(structural_times, 0.95),
    max_structural_scan_lines = model.metrics.max_structural_scan,
    max_structural_reconciled_cells = model.metrics.max_structural_cells,
    full_parse_count = model.metrics.full_parse_count,
    body_edit_count = model.metrics.body_edit_count,
    structural_reconcile_count = model.metrics.structural_reconcile_count,
  }

  assert_budget("initial parse p95", metrics.initial_parse_p95_ms, 100)
  assert_budget("initial model attach", metrics.initial_model_attach_ms, 100)
  assert_budget("body edit p95", metrics.body_edit_p95_ms, 5)
  assert_budget("body edit p99", metrics.body_edit_p99_ms, 10)
  assert_budget("structural edit p95", metrics.structural_edit_p95_ms, 16)
  assert(metrics.full_parse_count == 1, "ordinary and structural edits must not trigger a full parse")
  assert(metrics.max_structural_scan_lines <= 12, "structural recovery escaped the neighboring opener boundary")
  assert(metrics.max_structural_reconciled_cells <= 2, "structural reconciliation touched unrelated cells")

  model:detach()
  print("Frontend benchmark " .. vim.json.encode(metrics))
  return metrics
end

return M
