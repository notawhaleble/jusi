-- Explicit tab cleanup captures projections, not whatever cell/window is current
-- when asynchronous backend cleanup finishes.
local M = {}
local function windows(buf)
  local result = {}
  if not buf or not vim.api.nvim_buf_is_valid(buf) then return result end
  for _, win in ipairs(vim.fn.win_findbuf(buf)) do
    if vim.api.nvim_win_is_valid(win) then result[#result+1] = win end
  end
  return result
end
function M.close(session, options)
  local opts = options or {}
  local tab = vim.api.nvim_get_current_tabpage()
  session.tab_cleanup = session.tab_cleanup or {}
  if session.tab_cleanup[tab] then return end
  local notebook_windows, cells = {}, {}
  for _, win in ipairs(windows(session.buf)) do
    if vim.api.nvim_win_get_tabpage(win) == tab then notebook_windows[#notebook_windows+1] = win end
  end
  local function capture(buf, cell_id)
    local views, shared = {}, false
    for _, win in ipairs(windows(buf)) do
      if vim.api.nvim_win_get_tabpage(win) == tab then views[#views+1] = { win = win, buf = buf }
      else shared = true end
    end
    local entry = cells[cell_id] or { views = {}, shared = false }
    for _, view in ipairs(views) do entry.views[#entry.views+1] = view end
    entry.shared = entry.shared or shared
    cells[cell_id] = entry
  end
  for id, surface in pairs(session.presentation and session.presentation.surfaces or {}) do
    if not surface.closed then capture(surface.buf, id) end
  end
  for _, surface in pairs(session.interactive and session.interactive.surfaces or {}) do
    if not surface.closed then capture(surface.buf, surface.client.cell_id) end
  end
  local token = {}
  session.tab_cleanup[tab] = token
  local function current()
    return session.tab_cleanup[tab] == token and vim.api.nvim_tabpage_is_valid(tab)
      and (not opts.is_current or opts.is_current())
  end
  local function close_view(win, buf, force)
    if not current() or not vim.api.nvim_win_is_valid(win)
        or vim.api.nvim_win_get_tabpage(win) ~= tab or vim.api.nvim_win_get_buf(win) ~= buf then return end
    local ok, error = pcall(vim.api.nvim_win_close, win, force)
    if not ok and opts.on_error then opts.on_error(tostring(error)) end
  end
  local remaining, failed = 0, false
  local function finish()
    if not current() then session.tab_cleanup[tab] = nil; return end
    if not failed then
      for _, win in ipairs(notebook_windows) do
        -- Recount after each close; never remove the last notebook view, even
        -- if users changed the layout while the command was in flight.
        if #windows(session.buf) > 1 then close_view(win, session.buf, false) end
      end
    end
    session.tab_cleanup[tab] = nil
  end
  local pending = {}
  for cell_id, entry in pairs(cells) do
    if #entry.views > 0 then
      if entry.shared then
        for _, view in ipairs(entry.views) do close_view(view.win, view.buf, true) end
      else pending[#pending+1] = cell_id end
    end
  end
  table.sort(pending)
  remaining = #pending
  if remaining == 0 then finish(); return true end
  for _, cell_id in ipairs(pending) do
    session.lifecycle:close_cell(cell_id, function(ok)
      failed = failed or not ok
      remaining = remaining - 1
      if remaining == 0 then finish() end
    end)
  end
  return true
end
return M
