local M = {}
function M.run()
  local protocol = require("jusi.protocol")
  local function fixture(path) return vim.json.decode(table.concat(vim.fn.readfile("protocol/fixtures/v1/" .. path), "\n")) end
  local scenario = fixture("scenarios/cell-editing.json")
  local catalog = scenario.catalog
  assert(vim.deep_equal(catalog, fixture("valid/plugin-catalog-editing.json")))
  assert(protocol.validate_plugin_catalog(catalog))
  for _, name in ipairs({ "unsafe", "command" }) do assert(not protocol.validate_plugin_catalog(fixture("invalid/plugin-catalog-editing-" .. name .. ".json"))) end
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
    "╭──", '"""unclosed', "╰──", "╭──", "if True:", "    print(42)", "", "╰──",
    "╭──", "%%sql main", "select * from test", "╰──",
  })
  local model = require("jusi.notebook").attach(buf)
  local controller = { plugin_catalog = catalog }
  local editor = require("jusi.editing").new(model, controller)
  model.on_text_changed = function(ids) editor:changed(ids) end
  model.on_cells_changed = function(ids) editor:changed(ids, true) end
  model.on_cells_retired = function(ids) for _, id in ipairs(ids) do editor:retire(id) end end
  local cells = model:ordered_cells()
  editor:refresh()
  local function groups(id)
    return vim.api.nvim_buf_get_extmarks(buf, assert(editor.namespaces[id]), 0, -1, { details = true })
  end
  local function has_group(id, row, group)
    for _, mark in ipairs(groups(id)) do if mark[2] == row and mark[4].hl_group == group then return true end end
    return false
  end
  assert(has_group(cells[1].id, 1, "String"))
  assert(has_group(cells[2].id, 4, "Statement"), "unterminated previous string leaked into next cell")
  assert(has_group(cells[3].id, 9, "PreProc"))
  assert(has_group(cells[3].id, 10, "Statement"))
  assert(editor:indent(5) == 0, "first body line inherited previous cell indentation")
  assert(editor:indent(7) == 4)
  assert(editor:indent(8) == 0, "closer must stay unindented")
  -- Provider-specific catalog information is selected only by exact handoff.
  editor:event({ kind = "execution.started", payload = { cell_id = cells[3].id, execution_id = "exe_sql" } })
  editor:event({ kind = "client.created", payload = { cell_id = cells[3].id, execution_id = "exe_sql",
    plugin_id = scenario.resolved_plugin_id, family_id = "sql" } })
  assert(editor:context(cells[3]).syntax == "pgsql")
  vim.api.nvim_buf_set_lines(buf, 9, 10, false, { "%%sql other" })
  assert(editor:context(cells[3]).syntax == "sql", "alias edit retained stale exact provider")
  editor:client({ cell_id = cells[3].id, execution_id = "exe_sql", plugin_id = scenario.resolved_plugin_id, family_id = "sql" })
  assert(editor:context(cells[3]).syntax == "sql", "late handoff overrode edited magic")
  vim.api.nvim_buf_set_lines(buf, 9, 10, false, { "%%sql main" })
  editor:client({ cell_id = cells[3].id, execution_id = "exe_sql", plugin_id = scenario.resolved_plugin_id, family_id = "sql" })
  assert(editor:context(cells[3]).syntax == "sql", "restored header revived stale provider attribution")
  -- Real Insert-mode newline uses the isolated native Python indent expression.
  vim.api.nvim_win_set_cursor(0, { 5, 0 })
  vim.api.nvim_feedkeys("A" .. vim.api.nvim_replace_termcodes("<CR>x<Esc>", true, false, true), "xt", false)
  assert(vim.api.nvim_buf_get_lines(buf, 5, 6, false)[1] == "    x")
  editor:refresh()
  assert(has_group(cells[2].id, 4, "Statement"))
  local checked_insert = false
  _G.JusiEditingInsertCheck = function()
    assert(vim.fn.mode():sub(1, 1) == "i", "test left Insert mode before checking highlighting")
    assert(vim.wait(1000, function() return has_group(cells[2].id, 4, "Constant") end), "highlighting did not update during Insert mode")
    checked_insert = true
  end
  vim.api.nvim_win_set_cursor(0, { 5, 0 })
  vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes("ccreturn 99<Cmd>lua JusiEditingInsertCheck()<CR><Esc>", true, false, true), "xt", false)
  _G.JusiEditingInsertCheck = nil
  assert(checked_insert, "Insert-mode highlighting check did not complete")
  editor:close()
  assert(vim.fn.jobwait({ editor.job }, 1000)[1] ~= -1, "editing worker survived close")
  model:detach()
  vim.api.nvim_buf_delete(buf, { force = true })

  -- Only whole cells intersecting a window are materialized, including the
  -- union of two different views of the same notebook.
  local viewbuf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(viewbuf)
  local lines = {}
  for i = 1, 60 do vim.list_extend(lines, { "╭──", "print(" .. i .. ")", "╰──" }) end
  vim.api.nvim_buf_set_lines(viewbuf, 0, -1, false, lines)
  local view = require("jusi.editing").attach(viewbuf)
  local first_win = vim.api.nvim_get_current_win()
  local second_win = vim.api.nvim_open_win(viewbuf, false, { split = "below", height = 8 })
  vim.api.nvim_win_call(first_win, function() vim.cmd("normal! ggzt") end)
  vim.api.nvim_win_call(second_win, function() vim.cmd("normal! Gzb") end)
  view:refresh()
  local all = view.model:ordered_cells()
  assert(view.cache[all[1].id] and view.cache[all[60].id])
  assert(not view.cache[all[30].id], "offscreen cells were highlighted")
  vim.api.nvim_win_close(second_win, true)
  view:refresh()
  assert(not view.cache[all[60].id])
  local requests, original_request = 0, view.request
  view.request = function(self, request)
    if request.action == "highlight" then requests = requests + 1 end
    return original_request(self, request)
  end
  view:refresh()
  assert(requests == 0, "unchanged visible cells were reevaluated")
  vim.api.nvim_buf_set_lines(viewbuf, 1, 2, false, { 'print("α")' })
  view:refresh()
  assert(requests == 1, "one body edit reevaluated unrelated cells")
  local long_body = { '"""start' }
  for _ = 1, 30 do table.insert(long_body, "inside a multiline string") end
  table.insert(long_body, '"""')
  vim.api.nvim_buf_set_lines(viewbuf, 1, 2, false, long_body)
  vim.api.nvim_win_call(first_win, function() vim.cmd("normal! 20Gzt") end)
  view:refresh()
  local spans = vim.api.nvim_buf_get_extmarks(viewbuf, assert(view.namespaces[all[1].id]), 0, -1, { details = true })
  local has_string = false
  for _, span in ipairs(spans) do if span[2] == 19 and span[4].hl_group == "String" then has_string = true end end
  assert(has_string, "offscreen prefix of partially visible cell was not parsed")
  vim.api.nvim_buf_delete(viewbuf, { force = true })
  assert(view.closed and vim.fn.jobwait({ view.job }, 1000)[1] ~= -1)
end
return M
