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
  assert(vim.fn.isdirectory(service.artifact_candidate.directory) == 0)
end

local function test_inaccessible_shared_directory_keeps_service_usable()
  local service = local_service.start({ command = { '.venv/bin/python', '-c',
    "import os; os.environ['JUSI_SHARED_ARTIFACT_ROOT']='/unavailable/jusi-stage'; from jusi.__main__ import main; main(['serve', '--host', '127.0.0.1', '--port', '0', '--owner-stdin'])" } })
  assert(vim.wait(8000, function() return service.state ~= 'starting' end, 10))
  assert(service.state == 'running' and not service.shared_artifacts, vim.inspect(service.failure))
  service:stop()
  assert(vim.wait(5000, function() return service.state == 'stopped' end, 10))
  assert(vim.fn.isdirectory(service.artifact_candidate.directory) == 0)
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
  test_inaccessible_shared_directory_keeps_service_usable()
end

return M
