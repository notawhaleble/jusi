local presentation_module = require("jusi.presentation")

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
  local second = assert(presentation:write("cell_a", output("\27[31mred\27[0m")))
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

function M.run()
  test_text_and_ansi_share_native_terminal_surface()
  test_new_execution_replaces_only_its_cells_surface()
  test_unsupported_media_is_local_and_does_not_create_terminal()
  test_invalid_text_payload_is_a_local_surface_failure()
end

return M
