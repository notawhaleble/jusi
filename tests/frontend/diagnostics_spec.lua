local diagnostics = require("jusi.diagnostics")
local M = {}

function M.run()
  local failure = {
    failure_id = "fail_diagnostics", trace_id = "trace_diagnostics",
    layer = "service", operation = "start_kernel", reason = "invalid_request",
    message = "Explicit runtime configuration file does not exist",
    resource = { kind = "kernel", id = "krn_diagnostics" },
    details = { path = "/missing/jusi.toml", config = { password = "secret-marker" },
      environment = { TOKEN = "secret-marker" }, code = "secret-marker" },
    command = { "secret-marker" },
  }
  local summary = diagnostics.summary(failure)
  assert(summary:find("/missing/jusi.toml", 1, true))
  assert(summary:find(":JusiTrace trace_diagnostics", 1, true))
  diagnostics.record(failure)
  assert(#diagnostics.find("trace_diagnostics") == 1, "HTTP/SSE duplicates should collapse")
  failure.details.path = "mutated"
  local retained = diagnostics.find("trace_diagnostics")
  assert(retained[1].details.path == "/missing/jusi.toml", "retain detached data")
  assert(not vim.inspect(retained):find("secret-marker", 1, true), "never retain payload/config/argv")
  diagnostics.record({ trace_id = "trace_diagnostics", failure_id = "fail_caused",
    caused_by_failure_id = "fail_diagnostics", message = "caused failure" })
  assert(#diagnostics.find("trace_diagnostics") == 2, "retain distinct causes on one trace")
  for index = 1, 51 do
    diagnostics.record({ trace_id = "trace_bound_" .. index, message = string.rep("x", 40000) })
  end
  assert(#diagnostics.trace_ids() == 50)
  assert(#diagnostics.find("trace_diagnostics") == 0, "evict oldest records")
  assert(#diagnostics.find()[1].message < 17000, "bound individual strings")

  -- Exercise the public command's lost-startup-object regression with the real
  -- CLI parser. No service or kernel should be spawned by these invalid args.
  local jusi = require("jusi")
  local original_notify, notices = vim.notify, {}
  vim.notify = function(message) notices[#notices + 1] = message end
  jusi.setup()
  local source = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_current_buf(source)
  jusi.start_service({ buf = source, command = { vim.fn.getcwd() .. "/.venv/bin/jusi", "serve", "--config" } })
  assert(vim.wait(5000, function()
    return #notices > 0
  end, 10), "invalid startup must complete promptly")
  local latest = diagnostics.find()[1]
  assert(latest.layer == "frontend_service", vim.inspect(latest))
  assert(latest.process.exit_code == 2, vim.inspect(latest))
  assert(latest.process.stderr_excerpt:find("expected one argument", 1, true))
  assert(notices[#notices]:find("expected one argument", 1, true), "notification should expose parser cause")
  assert(not jusi._sessions[source], "failed startup must not create a session")
  vim.api.nvim_buf_delete(source, { force = true })
  vim.cmd("JusiTrace")
  local trace_buffer = vim.api.nvim_get_current_buf()
  local text = table.concat(vim.api.nvim_buf_get_lines(trace_buffer, 0, -1, false), "\n")
  assert(text:find(latest.trace_id, 1, true))
  assert(text:find("expected one argument", 1, true), "stderr survives notebook destruction")
  assert(not vim.bo[trace_buffer].modifiable and not vim.bo[trace_buffer].swapfile)
  vim.api.nvim_buf_delete(trace_buffer, { force = true })
  vim.cmd("JusiTrace " .. latest.trace_id)
  vim.api.nvim_buf_delete(vim.api.nvim_get_current_buf(), { force = true })
  vim.cmd("JusiTrace trace_not_retained")
  assert(notices[#notices]:find("No retained Jusi failure", 1, true))
  vim.notify = original_notify
end

return M
