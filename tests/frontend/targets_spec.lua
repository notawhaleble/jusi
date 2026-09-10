local M = {}
function M.run()
  local validate = require("jusi.targets").validate
  validate({ dev = { kind = "local", command = { "jusi", "serve" } },
    remote = { kind = "remote", base_url = "https://target.example:8765", kernel_name = "python3" } })
  for _, target in ipairs({
    { kind = "remote", base_url = "http://target", command = { "jusi", "serve" } },
    { kind = "local", base_url = "http://target" }, { kind = "remote" },
    { kind = "local", command = {} }, { kind = "local", timeout_ms = -1 },
    { kind = "local", typo = true },
  }) do assert(not pcall(validate, { bad = target }), vim.inspect(target)) end
  local jusi = require("jusi")
  jusi.setup({ targets = { zebra = { kind = "local" }, alpha = { kind = "local" } } })
  assert(vim.deep_equal(vim.fn.getcompletion("JusiStart ", "cmdline"), { "alpha", "zebra" }))
  jusi.setup({ targets = { ["local"] = { kind = "local" } } })
end
return M
