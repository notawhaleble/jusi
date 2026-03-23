from __future__ import annotations


class CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def session_updated(self, notebook_id: str, payload: dict) -> None:
        self.events.append({"type": "session_updated", "notebook_id": notebook_id, "payload": payload})

    def prepared_updated(self, notebook_id: str, payload: dict) -> None:
        self.events.append({"type": "prepared_updated", "notebook_id": notebook_id, "payload": payload})

    def cell_updated(self, notebook_id: str, payload: dict) -> None:
        self.events.append({"type": "cell_updated", "notebook_id": notebook_id, "payload": payload})

