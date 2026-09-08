require("jusi.editing").attach(0)
local undo = "lua require('jusi.editing').detach_standalone(vim.api.nvim_get_current_buf())"
vim.b.undo_ftplugin = vim.b.undo_ftplugin and (vim.b.undo_ftplugin .. " | " .. undo) or undo
