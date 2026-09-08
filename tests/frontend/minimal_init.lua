-- Tests must not load installed user plugins, including the sibling 0.x Jusi.
vim.opt.packpath = { vim.env.VIMRUNTIME }
vim.opt.runtimepath = { vim.fn.getcwd(), vim.env.VIMRUNTIME }
