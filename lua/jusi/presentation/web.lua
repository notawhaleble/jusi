-- The host owns browser sessions; Jusi owns only their buffer projections.
local M = {}
local windows = require('jusi.presentation.window')

function M.available()
  return vim.fn.exists(':JusiWebBuffer') == 2
end

function M.open(url, opts)
  opts = opts or {}
  assert(M.available(), 'JusiWebBuffer is unavailable in this Neovim client')
  assert(type(url) == 'string' and not url:find('[%c]'), 'invalid web URL')
  local original = vim.api.nvim_get_current_win()
  local win = opts.win
  if win then assert(vim.api.nvim_win_is_valid(win), 'source client window closed') end
  local tab = win and vim.api.nvim_win_get_tabpage(win) or vim.api.nvim_get_current_tabpage()
  local reuse = opts.reuse_buf and windows.find(opts.reuse_buf, tab)
  local previous = reuse and opts.reuse_buf
  local scratch
  if reuse then
    win = reuse
  else
    scratch = vim.api.nvim_create_buf(false, true)
    local created, value = pcall(vim.api.nvim_open_win, scratch, false,
      { split = 'below', win = win or original, height = opts.height or 12 })
    if not created then vim.api.nvim_buf_delete(scratch, { force = true }); error(value) end
    win = value
  end
  local buf
  local ok, err = pcall(vim.api.nvim_win_call, win, function()
    -- Structured command invocation: URL characters never become Ex commands.
    vim.api.nvim_cmd({ cmd = 'JusiWebBuffer', args = { url } }, {})
    buf = vim.api.nvim_get_current_buf()
    vim.bo[buf].bufhidden = 'hide'
    vim.bo[buf].swapfile = false
    if opts.cell_id then
      vim.b[buf].jusi_role = 'output'
      vim.b[buf].jusi_notebook_id = opts.notebook_id
      vim.b[buf].jusi_cell_id = opts.cell_id
      vim.b[buf].jusi_execution_id = opts.execution_id
      require('jusi.focus').attach(buf)
      require('jusi.output_ids').attach(buf)
    end
  end)
  if not ok and vim.api.nvim_win_is_valid(win) then buf = vim.api.nvim_win_get_buf(win) end
  if scratch and buf ~= scratch and vim.api.nvim_buf_is_valid(scratch) then vim.api.nvim_buf_delete(scratch, { force = true }) end
  if not ok then
    if vim.api.nvim_win_is_valid(win) then
      if previous and vim.api.nvim_buf_is_valid(previous) then vim.api.nvim_win_set_buf(win, previous)
      elseif not previous then vim.api.nvim_win_close(win, true) end
    end
    if buf and buf ~= previous and vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_delete(buf, { force = true }) end
    error(err)
  end
  if opts.enter then vim.api.nvim_set_current_win(win)
  elseif vim.api.nvim_win_is_valid(original) then vim.api.nvim_set_current_win(original) end
  local surface = { buf = buf, execution_id = opts.execution_id }
  function surface:close()
    if self.closed then return end
    self.closed = true
    if vim.api.nvim_buf_is_valid(self.buf) then
      windows.close_for_buffer(self.buf)
      vim.api.nvim_buf_delete(self.buf, { force = true })
    end
  end
  return surface
end

local priority = { 'text/html', 'image/svg+xml', 'image/png', 'image/jpeg', 'image/gif', 'application/javascript', 'text/javascript' }
function M.render(bundle, opts)
  local mime
  for _, candidate in ipairs(priority) do
    if type(bundle[candidate]) == 'string' then mime = candidate; break end
  end
  if not mime then return nil end
  local content = bundle[mime]
  if mime == 'application/javascript' or mime == 'text/javascript' then
    -- Keep arbitrary source (including </script>) out of the HTML parser.
    content = '<!doctype html><meta charset="utf-8"><div id="jusi-output"></div>'
      .. '<script>var element = document.getElementById("jusi-output");'
      .. 'function showError(message) { var pre = document.createElement("pre");'
      .. 'pre.textContent = String(message); document.body.appendChild(pre); }'
      .. 'window.addEventListener("error", function(e) { showError(e.message); });'
      .. 'window.addEventListener("unhandledrejection", function(e) { showError(e.reason); });</script>'
      .. '<script src="data:text/javascript;base64,' .. vim.base64.encode(content) .. '"></script>'
  elseif mime ~= 'text/html' then
    local encoded = mime == 'image/svg+xml' and vim.base64.encode(content) or content:gsub('%s', '')
    assert(encoded:match('^[A-Za-z0-9+/=]*$'), 'invalid image base64')
    content = '<!doctype html><meta charset="utf-8"><style>body{margin:0}img{max-width:100%;height:auto}</style><img src="data:' .. mime .. ';base64,' .. encoded .. '">'
  end
  -- Local content is transferred through the existing ordered output channel;
  -- only the frontend creates a file URL, never the remote kernel target.
  local path = vim.fn.tempname() .. '.html'
  local fd = assert(vim.uv.fs_open(path, 'wx', 384))
  local written, err = vim.uv.fs_write(fd, content, 0)
  vim.uv.fs_close(fd)
  if written ~= #content then os.remove(path); error(err or 'incomplete web content write') end
  local url = vim.uri_from_fname(path)
  local ok, surface = pcall(M.open, url, opts)
  if not ok then os.remove(path); error(surface) end
  local close = surface.close
  local autocmd = vim.api.nvim_create_autocmd('BufWipeout', { buffer = surface.buf, once = true,
    callback = function() os.remove(path) end })
  function surface:close()
    close(self)
    pcall(vim.api.nvim_del_autocmd, autocmd)
    os.remove(path)
  end
  return surface
end
return M
