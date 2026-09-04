local jusi = require("jusi")

local M = {}

local function wait_for(timeout, predicate, message)
  assert(vim.wait(timeout, predicate, 10), message)
end

local function terminal_text(buf)
  return table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
end

function M.run()
  local original_notify = vim.notify
  local original_pythonpath = vim.env.PYTHONPATH
  vim.notify = function() end
  vim.env.PYTHONPATH = vim.fn.getcwd() .. "/tests/fixtures/sqlite_plugin"
    .. (original_pythonpath and (":" .. original_pythonpath) or "")

  local database = vim.fn.tempname() .. ".sqlite"
  local config = vim.fn.tempname() .. ".toml"
  local setup = vim.system({
    ".venv/bin/python",
    "-c",
    "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('create table numbers(value integer)'); c.execute('insert into numbers values (2)'); c.commit(); c.close()",
    database,
  }, { text = true }):wait(5000)
  assert(setup.code == 0, setup.stderr)
  vim.fn.writefile({
    "[sql.main]",
    'provider = "sqlite"',
    'path = "' .. database:gsub('\\', '\\\\'):gsub('"', '\\"') .. '"',
  }, config)

  jusi.setup({
    terminal_bridge_command = { ".venv/bin/python", "-m", "jusi", "terminal-bridge" },
  })
  local buf
  local service
  local ok, failure = xpcall(function()
    buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
      "╭──", "%%sql main", "select value from numbers", "╰──",
    })
    vim.api.nvim_win_set_buf(0, buf)
    service = jusi.start_service({
      buf = buf,
      command = { ".venv/bin/python", "-m", "jusi", "serve", "--config", config },
      timeout_ms = 8000,
    })
    wait_for(8000, function()
      local session = jusi._sessions[buf]
      return session and session.controller.transport_state == "connected"
    end, "SQLite fixture service did not connect")
    local session = jusi._sessions[buf]
    jusi.start_kernel(buf)
    wait_for(10000, function()
      return session.controller.kernel_state == "on"
    end, "SQLite fixture kernel did not start")

    jusi.execute(buf, 1)
    wait_for(10000, function()
      return next(session.interactive.surfaces) ~= nil
    end, "SQLite VisiData terminal surface was not projected")
    local _, record = next(session.interactive.surfaces)
    local rendered = vim.wait(5000, function()
      local text = terminal_text(record.buf)
      return text:find("value", 1, true) ~= nil and text:find("2", 1, true) ~= nil
    end, 10)
    assert(rendered, "VisiData did not render the SQLite result:\n" .. terminal_text(record.buf)
      .. "\nclients=" .. vim.inspect(session.controller.clients)
      .. "\nsurfaces=" .. vim.inspect(session.controller.surfaces)
      .. "\nfailures=" .. vim.inspect(session.controller.failures))
    assert(terminal_text(record.buf):find("FileExistsError", 1, true) == nil,
      "isolated VisiData fixture attempted to persist user state:\n" .. terminal_text(record.buf))

    local first_client_id = record.client.client_id
    local first_buf = record.buf
    jusi.execute(buf, 1)
    wait_for(10000, function()
      if vim.tbl_count(session.controller.clients) ~= 1 then
        return false
      end
      local _, current = next(session.interactive.surfaces)
      return current ~= nil and current.client.client_id ~= first_client_id
    end, "re-execution did not replace the SQL cell artifact")
    assert(not vim.api.nvim_buf_is_valid(first_buf), "replaced SQL artifact buffer survived")
    local _, replacement = next(session.interactive.surfaces)
    wait_for(5000, function()
      local text = terminal_text(replacement.buf)
      return text:find("value", 1, true) ~= nil and text:find("2", 1, true) ~= nil
    end, "replacement VisiData artifact did not render")
    record = replacement

    vim.api.nvim_set_current_win(record.window)
    jusi.close()
    wait_for(5000, function()
      return next(session.controller.clients) == nil
        and next(session.controller.surfaces) == nil
        and next(session.interactive.surfaces) == nil
    end, "explicit SQL client close did not retire VisiData")
    assert(session.controller.kernel_state == "on")
    assert(not vim.api.nvim_buf_is_valid(record.buf))

    jusi.stop_service(buf)
    wait_for(8000, function()
      return service.state == "stopped" and jusi._sessions[buf] == nil
    end, "SQLite fixture service did not stop")
  end, debug.traceback)
  if buf and jusi._sessions[buf] then
    jusi._destroy_session(buf)
  elseif service and service.state ~= "stopped" then
    service:stop()
  end
  vim.fn.delete(config)
  vim.fn.delete(database)
  vim.env.PYTHONPATH = original_pythonpath
  vim.notify = original_notify
  if not ok then
    error(failure .. "\nservice stderr:\n" .. (service and service.stderr or ""))
  end
  print("SQLite VisiData client passed")
end

return M
