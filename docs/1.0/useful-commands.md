# Useful commands

These optional helpers belong in your `init.lua`. They use Jusi's Lua modules;
keep them alongside your matching frontend installation.

## JShell: a shell in the current directory

This helper opens or reuses a shell cell in a loaded notebook, using the current
file's directory, the current netrw directory, or Neovim's working directory
for an unnamed buffer. Without a range it submits `pwd` through Jusi's contextual
submission path. With a visual selection or line range it submits the selected
text as the shell body instead, preserving the cell's followup history.

It requires a Jusi 1.0-compatible [jusi-shell plugin](https://github.com/notawhaleble/jusi-shell)
installed in the target's service/kernel environment, and a started notebook
whose discovered palette includes `shell`. The shell runs at the kernel target;
for Docker or another remote target, the chosen directory must exist there
(use matching mounts/paths when using this helper).

Paste the following into `init.lua`:

```lua
local function jusi_shell_directory()
  local directory
  if vim.bo.filetype == 'netrw' then
    directory = vim.b.netrw_curdir or vim.t.netrw_curdir or vim.g.netrw_curdir
  end
  if not directory or directory == '' then
    local path = vim.api.nvim_buf_get_name(0)
    directory = path ~= '' and vim.fn.fnamemodify(path, ':h') or vim.fn.getcwd()
  end
  directory = vim.fn.fnamemodify(directory, ':p')
  return directory == '/' and directory or directory:gsub('/$', '')
end

local function shell_argument(value)
  if value:match('^[%w_@%%+=:,./~-]+$') then
    return value
  end
  return vim.fn.shellescape(value)
end

local function jusi_shell_error(message)
  vim.notify(tostring(message), vim.log.levels.ERROR, { title = 'JShell' })
end

local function set_jusi_magic_body(cell, body)
  if type(cell) ~= 'table' or vim.tbl_isempty(cell) then
    return nil, 'Jusi did not resolve a shell cell'
  end

  local bufnr = vim.api.nvim_get_current_buf()
  local editor = require('jusi.editing').attach(bufnr)
  local snapshot = editor.model:cell_snapshot(cell)
  if not snapshot then
    return nil, 'Jusi shell cell is no longer present'
  end

  -- body_start_row is the magic-header row; preserve it and replace everything
  -- through body_end_row, which excludes history and the closing delimiter.
  vim.api.nvim_buf_set_lines(bufnr, snapshot.body_start_row + 1, snapshot.body_end_row, false, { body })
  editor.model:flush()
  snapshot = editor.model:cell_snapshot(cell)
  if not snapshot then
    return nil, 'Jusi shell cell could not be refreshed after replacing its body'
  end
  vim.api.nvim_win_set_cursor(0, { snapshot.body_start_row + 2, 0 })
  return snapshot
end

vim.api.nvim_create_user_command('JShell', function(opts)
  local notebook = vim.trim(opts.args)
  local command = {
    args = '',
    fargs = { notebook, 'shell', '--cwd', shell_argument(jusi_shell_directory()) },
    bang = opts.range > 0,
    range = opts.range,
    line1 = opts.line1,
    line2 = opts.line2,
  }
  local ok, cell = pcall(require('jusi.palette').command, command)
  if not ok then
    jusi_shell_error(cell)
    return
  end
  if opts.range > 0 then
    return
  end

  vim.cmd.stopinsert()
  local snapshot, err = set_jusi_magic_body(cell, 'pwd')
  if not snapshot then
    jusi_shell_error(err)
    return
  end
  require('jusi').submit(vim.api.nvim_get_current_buf(), snapshot.body_start_row + 1)
end, {
  nargs = 1,
  range = true,
  complete = function(arglead)
    local command_line = 'J ' .. arglead
    local ok, aliases = pcall(require('jusi.palette').complete, arglead, command_line, #command_line)
    return ok and aliases or {}
  end,
  desc = 'Open a Jusi shell in the current file or netrw directory',
  force = true,
})
```

For a loaded `work.vipynb` notebook, run:

```vim
:JShell work
```

Notebook-name completion is available with Tab. Select shell commands in another
buffer and use `:'<,'>JShell work`, or submit a line range with `:1,3JShell work`.
The notebook opens in the current tab; execution reveals the shell output while
keeping focus in the notebook. Use Jusi's focus shortcut (Ctrl-\ twice) to enter
the terminal. See the [palette guide](usage.md#palette-and-focus) for the underlying
`:J` / `:J!` commands.

## Clean up the current notebook view

After a temporary JShell or other `:J` workflow, press `\Q` from the notebook
or an output in Normal mode (including notebook cell mode). The built-in
`:JusiCloseTab` command closes this notebook's outputs visible in the current
tab and removes its extra notebook windows. It retains the last notebook view
across all tabs and keeps the kernel running. No optional helper setup is needed.

Clients visible in another tab remain usable there; only their windows in the
current tab close. Hidden clients, unrelated project windows and application
exports are preserved. If client cleanup fails, the notebook view remains so
you can retry. Existing custom `\Q` mappings take precedence.
