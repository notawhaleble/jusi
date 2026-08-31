local specs = vim.fn.glob("tests/e2e/*_spec.lua", false, true)
table.sort(specs)
assert(#specs > 0, "no Neovim end-to-end specs discovered")
for _, path in ipairs(specs) do
  dofile(path).run()
end

print("Neovim end-to-end tests passed")
