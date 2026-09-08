local presentation_module = require("jusi.presentation")
local interactive_terminal = require("jusi.presentation.interactive_terminal")

local M = {}

local function equal(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    error((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual: " .. vim.inspect(actual))
  end
end

local function execution(number)
  return {
    execution_id = "exe_" .. number,
    client_id = "cli_" .. number,
  }
end

local function output(data, media_type)
  return {
    execution_id = "exe_1",
    client_id = "cli_1",
    output_kind = "stdout",
    media_type = media_type or "text/plain",
    data = data,
  }
end

local function terminal_lines(buf)
  return vim.api.nvim_buf_get_lines(buf, 0, -1, false)
end

local function test_text_and_ansi_share_native_terminal_surface()
  local presentation = presentation_module.new({ notebook_id = "nb_test" })
  presentation:start_execution("cell_a", execution("1"))
  local first = assert(presentation:write("cell_a", output("plain\n")))
  local second = assert(presentation:write("cell_a", output("\27[3")))
  assert(presentation:write("cell_a", output("1mred\27[0")))
  assert(presentation:write("cell_a", output("m")))
  equal(first.buf, second.buf, "stream chunks for one cell must share a surface")
  assert(vim.wait(1000, function()
    local lines = terminal_lines(first.buf)
    return lines[1] == "plain" and lines[2] == "red"
  end, 10), vim.inspect(terminal_lines(first.buf)))
  local rendered = table.concat(terminal_lines(first.buf), "\n")
  assert(not rendered:find("\27", 1, true), "Neovim, not Jusi, must consume ANSI escapes")
  equal(vim.bo[first.buf].buftype, "terminal")
  equal(vim.b[first.buf].jusi_cell_id, "cell_a")
  presentation:close()
end

local function test_new_execution_replaces_only_its_cells_surface()
  local presentation = presentation_module.new({ notebook_id = "nb_test" })
  presentation:start_execution("cell_a", execution("1"))
  local old_a = assert(presentation:write("cell_a", output("old"))).buf
  presentation:start_execution("cell_b", execution("2"))
  local buf_b = assert(presentation:write("cell_b", output("other"))).buf

  presentation:start_execution("cell_a", execution("3"))
  assert(not vim.api.nvim_buf_is_valid(old_a), "replaced execution surface must be cleaned up")
  assert(vim.api.nvim_buf_is_valid(buf_b), "another cell's surface must survive")
  local new_a = assert(presentation:write("cell_a", output("new"))).buf
  assert(new_a ~= old_a)
  presentation:close()
end

local function test_unsupported_media_is_local_and_does_not_create_terminal()
  local failures = {}
  local presentation = presentation_module.new({
    notebook_id = "nb_test",
    on_failure = function(failure)
      table.insert(failures, failure)
    end,
  })
  local surface, failure = presentation:write("cell_a", output("{}", "application/json"))
  equal(surface, nil)
  equal(failure.layer, "frontend_presentation")
  equal(failure.reason, "unsupported")
  equal(failure.scope, "cell")
  equal(#failures, 1)
  equal(presentation:buffer_for_cell("cell_a"), nil)
  presentation:close()
end

local function test_invalid_text_payload_is_a_local_surface_failure()
  local presentation = presentation_module.new({ notebook_id = "nb_test" })
  local malformed = output("text")
  malformed.data = nil
  local surface, failure = presentation:write("cell_a", malformed)
  equal(surface, nil)
  equal(failure.layer, "frontend_presentation")
  equal(failure.scope, "cell")
  equal(presentation:buffer_for_cell("cell_a") ~= nil, true)
  presentation:close()
end

local function test_interactive_terminal_is_a_generic_bridge_projection()
  local launches = {}
  local manager = interactive_terminal.new({
    base_url = "https://target.example/jusi/",
    command = { "/opt/jusi", "terminal-bridge" },
    height = 9,
    notebook_buf = vim.api.nvim_create_buf(false, true),
    notebook_id = "nb_test",
    launch = function(options)
      table.insert(launches, options)
      return { buf = vim.api.nvim_create_buf(false, true), job_id = 999 }
    end,
  })
  local client = { client_id = "cli_test", cell_id = "cell_a" }
  local surface = { surface_id = "srf_test", client_id = "cli_test", kind = "terminal" }
  local record = assert(manager:open(surface, client))
  equal(launches[1].command, {
    "/opt/jusi", "terminal-bridge", "https://target.example/jusi", "srf_test",
  })
  equal(manager:buffer_for_cell("cell_a"), record.buf)
  manager:reconcile({ srf_test = surface }, { cli_test = client })
  equal(#launches, 1, "authoritative replay must not duplicate a live projection")
  local output_window = vim.api.nvim_open_win(record.buf, false, { split = "below" })
  manager:reconcile({}, {})
  assert(not vim.api.nvim_win_is_valid(output_window), "retired interactive surface must close its split")
  equal(manager:buffer_for_cell("cell_a"), nil)
  assert(not vim.api.nvim_buf_is_valid(record.buf), "retired surface must delete its terminal buffer")
end

local function test_input_echo_and_closed_execution_fencing()
  local presentation = presentation_module.new({ notebook_id = "nb_input_echo" })
  local callbacks = presentation:controller_callbacks()
  local request = { execution_id = "exe_input", input_request_id = "inp_input", prompt = "lalala: ", password = false }
  callbacks.on_execution_started("cell_input", request)
  callbacks.on_input_requested("cell_input", request)
  callbacks.on_input_replied("cell_input", request, "ololo")
  local buf = assert(presentation:buffer_for_cell("cell_input"))
  assert(vim.wait(1000, function() return vim.api.nvim_buf_get_lines(buf, 0, 1, false)[1] == "lalala: ololo" end))
  presentation:retire_execution("cell_input", request.execution_id)
  callbacks.on_output("cell_input", { execution_id = request.execution_id, media_type = "text/plain", data = "late traceback" })
  callbacks.on_input_requested("cell_input", request)
  callbacks.on_input_replied("cell_input", request, "late echo")
  assert(presentation:buffer_for_cell("cell_input") == nil, "closed execution must not resurrect")
  local new_request = { execution_id = "exe_new", input_request_id = "inp_new", prompt = "password: ", password = true }
  callbacks.on_execution_started("cell_input", new_request)
  callbacks.on_input_requested("cell_input", new_request)
  callbacks.on_input_replied("cell_input", new_request, "PRIVATE_VALUE")
  local new_buf = assert(presentation:buffer_for_cell("cell_input"))
  presentation:retire_execution("cell_input", request.execution_id)
  assert(presentation:buffer_for_cell("cell_input") == new_buf, "late close must preserve newer execution")
  assert(vim.wait(1000, function() return vim.api.nvim_buf_get_lines(new_buf, 0, 1, false)[1] == "password: [input accepted]" end))
  assert(not table.concat(vim.api.nvim_buf_get_lines(new_buf, 0, -1, false), "\n"):find("PRIVATE_VALUE", 1, true))
  presentation:close()
end

local function test_close_removes_output_windows_only()
  local notebook_buf = vim.api.nvim_create_buf(false, true)
  local notebook_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(notebook_win, notebook_buf)
  local presentation = presentation_module.new({ notebook_id = "nb_windows" })
  presentation:start_execution("cell_a", execution("1"))
  local surface = assert(presentation:write("cell_a", output("visible")))
  local first = vim.api.nvim_open_win(surface.buf, false, { split = "below", win = notebook_win })
  local second = vim.api.nvim_open_win(surface.buf, false, { split = "right", win = first })
  local reused = vim.api.nvim_open_win(surface.buf, false, { split = "below", win = first })
  vim.api.nvim_win_set_buf(reused, notebook_buf)
  presentation:close_cell("cell_a")
  assert(not vim.api.nvim_win_is_valid(first) and not vim.api.nvim_win_is_valid(second))
  assert(vim.api.nvim_win_is_valid(reused) and vim.api.nvim_win_get_buf(reused) == notebook_buf)
  assert(vim.api.nvim_get_current_win() == notebook_win)
  assert(not vim.api.nvim_buf_is_valid(surface.buf))
  presentation:close_cell("cell_a")
  vim.api.nvim_win_close(reused, true)
  -- Closing the only remaining window is forbidden by Neovim; still retire
  -- its output buffer without closing the editor or leaving stale output.
  presentation:start_execution("cell_a", execution("2"))
  local last = assert(presentation:write("cell_a", output("last")))
  for _, win in ipairs(vim.fn.win_findbuf(last.buf)) do vim.api.nvim_win_close(win, true) end
  vim.api.nvim_win_set_buf(notebook_win, last.buf)
  presentation:close_cell("cell_a")
  assert(vim.api.nvim_win_is_valid(notebook_win) and not vim.api.nvim_buf_is_valid(last.buf))
  vim.api.nvim_win_set_buf(notebook_win, notebook_buf)
  presentation:close()
  vim.api.nvim_buf_delete(notebook_buf, { force = true })
end

function M.run()
  test_close_removes_output_windows_only()
  test_input_echo_and_closed_execution_fencing()
  test_text_and_ansi_share_native_terminal_surface()
  test_new_execution_replaces_only_its_cells_surface()
  test_unsupported_media_is_local_and_does_not_create_terminal()
  test_invalid_text_payload_is_a_local_surface_failure()
  test_interactive_terminal_is_a_generic_bridge_projection()
end

return M
