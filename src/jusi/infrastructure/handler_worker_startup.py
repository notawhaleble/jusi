from __future__ import annotations

from dataclasses import dataclass

from jusi.domain.models import ExecutableCell


@dataclass(frozen=True)
class HandlerWorkerStartup:
    notebook_id: str
    session_id: str
    client_id: str
    cell_id: int
    handler_id: str
    magic_name: str
    cell: ExecutableCell
    content: str = ""
    meta: dict[str, object] | None = None
