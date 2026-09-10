-- Per-invocation development frontend. Keep the user's ordinary Neovim config.
local source = debug.getinfo(1, "S").source:sub(2)
local root = vim.fn.fnamemodify(source, ":p:h:h")
vim.g.loaded_jusi = 1 -- 0.x plugin/jusi.vim loading guard; distinct from 1.0.
vim.opt.runtimepath:prepend(root)
require("jusi").setup({
  service_command = { root .. "/.venv/bin/jusi", "serve" },
  terminal_bridge_command = { root .. "/.venv/bin/jusi", "terminal-bridge" },
  targets = {
    jusi = { kind = "local", command = { root .. "/.venv/bin/jusi", "serve" }, kernel_name = "python3" },
  },
})
vim.api.nvim_create_autocmd("VimEnter", {
  once = true,
  callback = function()
    -- Start packages were added after --cmd. Remove legacy runtime lookup now;
    -- its plugin was fenced by the guard above, and 1.0 owns .vipynb detection.
    local paths = vim.opt.runtimepath:get()
    vim.opt.runtimepath = vim.tbl_filter(function(path)
      return not path:match("/jusivim/?$") and not path:match("/jusivim/after/?$")
    end, paths)
  end,
})
