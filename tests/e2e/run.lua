local original_home = vim.env.HOME
local test_home = vim.fn.tempname()
vim.fn.mkdir(test_home, "p")
vim.env.HOME = test_home

local ok, failure = xpcall(function()
  local specs = vim.fn.glob("tests/e2e/*_spec.lua", false, true)
  table.sort(specs)
  assert(#specs > 0, "no Neovim end-to-end specs discovered")
  for _, path in ipairs(specs) do
    dofile(path).run()
  end
end, debug.traceback)

vim.env.HOME = original_home
vim.fn.delete(test_home, "rf")
if not ok then
  error(failure)
end

print("Neovim end-to-end tests passed")
