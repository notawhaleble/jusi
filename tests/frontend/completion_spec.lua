local completion = require("jusi.completion")
local notebook = require("jusi.notebook")
local controller_module = require("jusi.controller")
local protocol = require("jusi.protocol")
local M = {}
local function read(name)
  local file = assert(io.open("protocol/fixtures/v1/" .. name))
  local data = vim.json.decode(file:read("*a")); file:close(); return data
end
function M.run()
  local scenario = read("scenarios/completion.json")
  assert(protocol.validate_command(scenario.command, "complete"))
  for _, event in ipairs(scenario.events) do assert(protocol.validate_event(event)) end
  assert(protocol.validate_completion(scenario.completion, scenario.command.cursor_pos))
  for _, name in ipairs({ "completion-suffix-range", "completion-reversed-range", "completion-fractional-range" }) do
    assert(not protocol.validate_completion(read("invalid/" .. name .. ".json"), 3))
  end
  assert(not protocol.validate_command(read("invalid/completion-cursor-outside-body.json"), "complete"))
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(buf)
  local model = notebook.attach(buf)
  local requests = {}
  local controller = controller_module.new({ notebook = model, transport = {
    request = function(_, _, path, command, _, callback)
      requests[#requests + 1] = { path = path, command = command, callback = callback }
    end,
  } })
  controller.kernel_id, controller.kernel_state, controller.runtime_id = "kernel_test", "on", "runtime_test"
  local function source(body, row, col)
    local text = { "╭──" }
    vim.list_extend(text, vim.split(body, "\n", { plain = true }))
    text[#text + 1] = "╰──"
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, text)
    vim.api.nvim_win_set_cursor(0, { row + 2, col })
  end
  local function body() return table.concat(assert(model:body(model:cell_at_row(1).id)), "\n") end
  local function keys(text)
    vim.v.errmsg = ""
    vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes(text, true, false, true), "xt", false)
    assert(vim.v.errmsg == "", vim.v.errmsg)
  end
  local items
  _G.jusi_native_show = function()
    local ctx = assert(completion.capture(model, controller))
    assert(completion.show(ctx, { items = items }))
  end
  local suffix_ns = vim.api.nvim_create_namespace("jusi_native_suffix_test")
  local suffix_mark
  _G.jusi_native_check = function()
    assert(vim.deep_equal(vim.api.nvim_buf_get_extmark_by_id(buf, suffix_ns, suffix_mark, {}),
      { 1, #"from time import sleepla" }), "native edit damaged a suffix extmark")
    assert(vim.fn.pumvisible() == 1, "completion must use the native popup")
    assert(body() == "from time import sleeplalala")
  end
  vim.bo[buf].completeopt = "menuone"
  source("from time import slalala", 0, 18)
  suffix_mark = vim.api.nvim_buf_set_extmark(buf, suffix_ns, 1, #"from time import sla", {})
  items = { { text = "sleep", start = 17, ["end"] = 18 }, { text = "strftime", start = 17, ["end"] = 18 } }
  local maps = vim.api.nvim_buf_get_keymap(buf, "i")
  keys("i<Cmd>lua jusi_native_show()<CR><Cmd>lua jusi_native_check()<CR><C-n><C-y>X<Esc>")
  assert(body() == "from time import strftimeXlalala")
  assert(vim.deep_equal(maps, vim.api.nvim_buf_get_keymap(buf, "i")), "completion installed input mappings")
  source("from time import slalala", 0, 18)
  keys("i<Cmd>lua jusi_native_show()<CR><C-n><C-e><Esc>")
  assert(body() == "from time import slalala")
  -- Escape and Enter follow native semantics, not custom cancel/accept maps.
  source("from time import slalala", 0, 18)
  keys("i<Cmd>lua jusi_native_show()<CR><Esc>")
  assert(body() == "from time import sleeplalala", "Escape should preserve native preview")
  local original = "αβ\nselect schema.tTAIL\nafter"
  local pos = vim.fn.strchars("αβ\nselect schema.t")
  items = {
    { text = "table", start = pos - 1, ["end"] = pos },
    { text = "other.table", start = pos - #"schema.t", ["end"] = pos },
    { text = "γ\nrewritten", start = 0, ["end"] = pos },
  }
  source(original, 1, #"select schema.t")
  keys("i<Cmd>lua jusi_native_show()<CR><C-n><C-y><Esc>")
  assert(body() == "αβ\nselect other.tableTAIL\nafter")
  source(original, 1, #"select schema.t")
  keys("i<Cmd>lua jusi_native_show()<CR><C-n><C-n><C-y>X<Esc>")
  assert(body() == "γ\nrewrittenXTAIL\nafter", body())
  source(original, 1, #"select schema.t")
  keys("i<Cmd>lua jusi_native_show()<CR><C-n><C-n><Esc>")
  assert(body() == "γ\nrewrittenTAIL\nafter", "Escape must finalize the preserved native choice")
  source(original, 1, #"select schema.t")
  keys("i<Cmd>lua jusi_native_show()<CR><C-n><C-n><C-e><Esc>")
  assert(body() == original)
  -- Respect noselect/noinsert rather than forcing an initial preview.
  vim.bo[buf].completeopt = "menuone,noselect,noinsert"
  source(original, 1, #"select schema.t")
  keys("i<Cmd>lua jusi_native_show()<CR><C-e><Esc>")
  assert(body() == original)
  vim.bo[buf].completeopt = "menuone"
  source("select * from TAIL", 0, 14)
  items = { { text = "public.", start = 14, ["end"] = 14 } }
  keys("i<Cmd>lua jusi_native_show()<CR><C-y><Esc>")
  assert(body() == "select * from public.TAIL")
  -- Same-line edits may themselves insert multiple lines natively.
  source("αsTAIL", 0, #"αs")
  items = { { text = "sleep\nnext", start = 1, ["end"] = 2 } }
  keys("i<Cmd>lua jusi_native_show()<CR><C-y><Esc>")
  assert(body() == "αsleep\nnextTAIL")
  source("αsTAIL", 0, #"αs")
  keys("i<Cmd>lua jusi_native_show()<CR><C-e><Esc>")
  assert(body() == "αsTAIL")
  source("abcdTAIL", 0, 4)
  items = { { text = "X", start = 1, ["end"] = 2 } }
  keys("i<Cmd>lua jusi_native_show()<CR><C-y>Z<Esc>")
  assert(body() == "aXZcdTAIL", "acceptance lost the explicit replacement cursor")
  source(original, 1, #"select schema.t")
  items = { { text = "", start = 0, ["end"] = pos } }
  keys("i<Cmd>lua jusi_native_show()<CR><C-y><Esc>")
  assert(body() == "TAIL\nafter", "empty replacement failed to finalize its range")
  _G.jusi_native_request = function() completion.request(model, controller, function() end) end
  source("prTAIL", 0, 2)
  _G.jusi_native_resolve = function()
    assert(requests[1].command.body == "prTAIL" and requests[1].command.cursor_pos == 2)
    vim.api.nvim_buf_set_text(buf, 1, 0, 1, 2, { "changed" })
    requests[1].callback({ completion = { items = { { text = "print", start = 0, ["end"] = 2 } } } })
  end
  keys("i<Cmd>lua jusi_native_request()<CR><Cmd>lua jusi_native_resolve()<CR><Esc>")
  assert(body() == "changedTAIL" and not completion._active[buf])
  source("prTAIL", 0, 2)
  _G.jusi_native_resolve = function()
    controller.runtime_id = "runtime_replaced"
    requests[#requests].callback({ completion = { items = { { text = "print", start = 0, ["end"] = 2 } } } })
    assert(not completion._active[buf])
  end
  keys("i<Cmd>lua jusi_native_request()<CR><Cmd>lua jusi_native_resolve()<CR><Esc>")
  assert(body() == "prTAIL")
  controller.plugin_catalog = { plugins = { { families = { { magic_name = "sql" } } } } }
  source("%%time\nprTAIL", 1, 2)
  assert(completion.capture(model, controller))
  source("%%sql\nselect * from TAIL", 1, 14)
  assert(not completion.capture(model, controller))
  local cell_id = model:cell_at_row(1).id
  controller.clients.client_test = { client_id = "client_test", cell_id = cell_id,
    notebook_id = model.notebook_id, capabilities = { "complete" } }
  _G.jusi_native_resolve = function()
    assert(requests[#requests].command.client_id == "client_test")
    controller.clients = {}
    requests[#requests].callback({ completion = { items = { { text = "public", start = 0, ["end"] = 0 } } } })
    assert(not completion._active[buf])
  end
  keys("i<Cmd>lua jusi_native_request()<CR><Cmd>lua jusi_native_resolve()<CR><Esc>")
  assert(body() == "%%sql\nselect * from TAIL")
  _G.jusi_native_show, _G.jusi_native_check, _G.jusi_native_request, _G.jusi_native_resolve = nil, nil, nil, nil
  model:detach()
  vim.api.nvim_buf_delete(buf, { force = true })
end
return M
