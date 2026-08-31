local local_service = require("jusi.service.local")

local M = {}

local function test_spawn_failure_is_typed()
  local captured
  local service = local_service.start({ command = { "/definitely/not/a/jusi-service" } }, function(value, failure)
    assert(value == nil)
    captured = failure
  end)
  assert(captured ~= nil)
  assert(service.state == "stopped")
  assert(captured.layer == "frontend_service")
  assert(captured.operation == "service_start")
  assert(captured.reason == "spawn_failed")
  assert(captured.resource.kind == "service_process")
  assert(captured.trace_id ~= "")
end

local function test_invalid_readiness_preserves_bounded_stderr()
  local captured
  local service = local_service.start({
    command = {
      ".venv/bin/python",
      "-c",
      "import sys; sys.stderr.write('x' * 17000 + 'fatal\\n'); sys.stderr.flush(); print('not-json', flush=True)",
    },
    timeout_ms = 3000,
  }, function(value, failure)
    assert(value == nil)
    captured = failure
  end)
  assert(vim.wait(5000, function()
    return captured ~= nil
  end, 10), "invalid-readiness service did not terminate")
  assert(captured.reason == "readiness_failed")
  assert(captured.process.stderr_truncated == true)
  assert(captured.process.stderr_excerpt:sub(-6) == "fatal\n")
  assert(#captured.process.stderr_excerpt == 16 * 1024)
  assert(service.state == "stopped")
end

function M.run()
  test_spawn_failure_is_typed()
  test_invalid_readiness_preserves_bounded_stderr()
end

return M
