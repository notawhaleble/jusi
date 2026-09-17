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
  local function install(mode, key, callback, description)
    vim.api.nvim_buf_call(buf, function()
      if vim.fn.maparg(key, mode) == '' then
        vim.keymap.set(mode, key, callback, { buffer = buf, silent = true, desc = description })
        table.insert(owned, { mode = mode, key = key, callback = callback })
      end
    end)
  end
  vim.api.nvim_buf_call(buf, function()
    for suffix, callback in pairs(actions) do
      install('n', '\\' .. suffix, callback, 'Jusi notebook action')
    end
  end)
  install('i', '<C-Y>', function()
    -- Preserve native completion acceptance while the popup menu is visible.
    if vim.fn.pumvisible() == 1 then
      vim.api.nvim_feedkeys(vim.api.nvim_replace_termcodes('<C-Y>', true, false, true), 'n', false)
      return
    end
    require('jusi.cellmode').command('submit_and_edit')
  end, 'Submit Jusi cell and keep editing')
  return function()
    if not vim.api.nvim_buf_is_valid(buf) then return end
    vim.api.nvim_buf_call(buf, function()
      for _, mapping in ipairs(owned) do
        if vim.fn.maparg(mapping.key, mapping.mode, false, true).callback == mapping.callback then
          vim.keymap.del(mapping.mode, mapping.key, { buffer = buf })
        end
      end
    end)
  end
end
return M
