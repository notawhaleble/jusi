-- Client-owned documents and terminals are views of the same runtime client.
-- Independent exports never participate in client/tab cleanup selection.
local M = {}

function M.each(session, visit)
  for _, surface in pairs(session.interactive and session.interactive.surfaces or {}) do
    if not surface.closed then visit(surface.buf, surface.client) end
  end
  for client_id, slots in pairs(session.url_slots or {}) do
    local client = session.controller and session.controller.clients[client_id]
    if client then
      for _, page in pairs(slots) do
        if page.lifetime == 'client' and not page.closed then visit(page.buf, client) end
      end
    end
  end
end

function M.contains(session, buf)
  local found = false
  M.each(session, function(view) if view == buf then found = true end end)
  return found
end

return M
