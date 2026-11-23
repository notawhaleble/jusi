local M = {}

function M.run()
  local protocol = require("jusi.protocol")
  for name, expected in pairs({
    ["valid/display-update"] = true, ["invalid/display-update-id"] = false,
    ["valid/output-clear"] = true, ["invalid/output-clear-wait"] = false,
  }) do
    local path = "protocol/fixtures/v1/" .. name .. ".json"
    assert(protocol.validate_event(vim.json.decode(table.concat(vim.fn.readfile(path), "\n"))) == expected, name)
  end

  local p = require("jusi.presentation").new({ notebook_id = "nb_displays" })
  local function begin(cell, kernel)
    p:start_execution(cell, { execution_id = "exe_" .. cell, kernel_id = kernel })
  end
  local function text(cell, data)
    return p:write(cell, { execution_id = "exe_" .. cell, media_type = "text/plain", data = data })
  end
  local function display(cell, kind, id, data)
    p:write(cell, { execution_id = "exe_" .. cell, media_type = "application/vnd.jusi.mime-bundle.v1+json",
      data = vim.json.encode({ ["text/plain"] = data }), output_kind = kind, display_id = id,
      bundle_id = "out_test", bundle_end = true })
  end
  local function content(cell, expected)
    local buf = assert(p:buffer_for_cell(cell))
    local actual
    assert(vim.wait(2000, function()
      actual = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n"):gsub("\n+$", "")
      return actual == expected
    end), vim.inspect(actual))
  end
  local ok, err = xpcall(function()
    begin("first", "kernel_a")
    -- Exercise a spilled stream and its retained ANSI bytes on replay.
    local prefix = string.rep("x\r", 5000) .. "prefix "
    text("first", prefix)
    display("first", "display", "figure", string.rep("old\n", 100))
    text("first", " / ")
    display("first", "display", "figure", "old")
    text("first", " suffix")
    begin("second", "kernel_a")
    begin("other", "kernel_b")
    display("other", "display", "figure", "independent")
    display("second", "update", "figure", "new")
    content("first", "prefix new / new suffix")
    content("other", "independent")
    assert(not p:buffer_for_cell("second"))
    p:write("first", { execution_id = "exe_first", wait = true })
    content("first", "prefix new / new suffix")
    local function part(data, last)
      p:write("first", { execution_id = "exe_first", media_type = "application/vnd.jusi.mime-bundle.v1+json",
        data = data, output_kind = "display", bundle_id = "out_parts", bundle_end = last })
    end
    part('{"text/plain":', false)
    content("first", "prefix new / new suffix")
    part('"replacement"}', true)
    content("first", "replacement")
    display("second", "update", "figure", "stale")
    content("first", "replacement")
    p:write("first", { execution_id = "exe_first", wait = false })
    assert(not p:buffer_for_cell("first"))
    text("first", "after clear")
    content("first", "after clear")
  end, debug.traceback)
  p:close()
  assert(ok, err)
end

return M
