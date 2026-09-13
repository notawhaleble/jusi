local M = {}
function M.attach(buf)
  local owned = {}
  local function action(name, argument) return function() require('jusi.cellmode').command(name, argument) end end
  local actions = {
    a = action('insert', true), b = action('insert', false), x = action('delete'),
    c = action('edit'), y = action('copy'), p = action('paste'),
    h = function() require('jusi.history').command('toggle') end,
    j = function() require('jusi').submit() end,
    s = function() require('jusi').park() end,
    ['00'] = function() require('jusi').restart() end,
    ii = function() require('jusi').interrupt() end,
    q = function() require('jusi').close_number(vim.v.count) end,
    g = function() require('jusi').goto_number(vim.v.count) end,
  }
  vim.api.nvim_buf_call(buf, function()
    for suffix, callback in pairs(actions) do
      local key = '\\' .. suffix
      if vim.fn.maparg(key, 'n') == '' then
        vim.keymap.set('n', key, callback, { buffer = buf, silent = true, desc = 'Jusi notebook action' })
        owned[key] = callback
      end
    end
  end)
  return function()
    if not vim.api.nvim_buf_is_valid(buf) then return end
    vim.api.nvim_buf_call(buf, function()
      for key, callback in pairs(owned) do
        if vim.fn.maparg(key, 'n', false, true).callback == callback then vim.keymap.del('n', key, { buffer = buf }) end
      end
    end)
  end
end
return M
