-- An extra pipe owns the HTTP subprocess lifetime, independently of request
-- stdin. EOF also works when Neovim is killed and cannot run exit callbacks.
local M = {}
local script = [[
exec 4<&0
"$@" <&4 3<&- 4<&- &
child=$!
(
  read -r owner <&3
  kill -TERM "$child" 2>/dev/null
) 4<&- &
watcher=$!
trap 'kill -TERM "$child" "$watcher" 2>/dev/null' HUP INT TERM
wait "$child"
result=$?
kill -TERM "$watcher" 2>/dev/null
wait "$watcher" 2>/dev/null
exit "$result"
]]

function M.start(command, options, callback)
  local uv = vim.uv
  local stdin, stdout, stderr, owner = uv.new_pipe(false), uv.new_pipe(false), uv.new_pipe(false), uv.new_pipe(false)
  local pipes = { stdin, stdout, stderr, owner }
  local function close(pipe)
    if not pipe:is_closing() then pipe:close() end
  end
  local out, err = {}, {}
  local ended, out_done, err_done, delivered = false, false, false, false
  local result, process
  local function finish()
    if ended and out_done and err_done and not delivered then
      delivered = true
      result.stdout, result.stderr = table.concat(out), table.concat(err)
      callback(result)
    end
  end
  local args = { "-c", script, "jusi-http" }
  vim.list_extend(args, command)
  local pid
  process, pid = uv.spawn("sh", { args = args, stdio = pipes }, function(code, signal)
    ended, result = true, { code = code, signal = signal }
    close(owner)
    close(stdin)
    if process and not process:is_closing() then process:close() end
    finish()
  end)
  if not process then
    for _, pipe in ipairs(pipes) do close(pipe) end
    error("Cannot spawn HTTP process: " .. tostring(pid))
  end
  stdout:read_start(function(error, data)
    if options.stdout then options.stdout(error, data)
    elseif data then out[#out + 1] = data end
    if not data then out_done = true; close(stdout); finish() end
  end)
  stderr:read_start(function(error, data)
    if data then err[#err + 1] = data end
    if error then err[#err + 1] = tostring(error) end
    if not data then err_done = true; close(stderr); finish() end
  end)
  if options.stdin then
    stdin:write(options.stdin, function() close(stdin) end)
  else
    close(stdin)
  end
  return { pid = pid, kill = function() close(owner) end }
end

return M
