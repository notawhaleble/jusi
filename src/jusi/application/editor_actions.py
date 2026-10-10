from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time
import uuid
from typing import Any, Callable

from jusi.protocol import validate_editor_action, validate_attention_request
from .text_snapshot import TextSnapshot


class EditorActionError(ValueError):
    def __init__(self, message: str, reason: str = "conflict") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class _Action:
    metadata: dict[str, str]
    content: TextSnapshot
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, str] | None = None
    fetched: bool = False
    complete: bool = False


class EditorActionManager:
    """Client-owned delivery snapshots, independent of the ordinary work lane."""

    def __init__(self, publish: Callable[[dict[str, str]], None], *, timeout: float = 30.0,
                 publish_attention: Callable | None = None) -> None:
        self._publish = publish
        self._timeout = timeout
        self._lock = threading.RLock()
        self._clients: dict[str, dict[str, str]] = {}
        self._owners: dict[str, str] = {}
        self._connections: dict[str, set[str]] = {}
        self._actions: OrderedDict[str, _Action] = OrderedDict()
        self._publish_attention = publish_attention or (lambda item: None)
        self._attention: dict[str, dict[str, Any]] = {}
        self._capabilities: dict[str, tuple] = {}

    def inspection_lock(self):
        """Keep health's event cursor and client-channel snapshots consistent."""
        return self._lock

    def register(self, client) -> None:
        with self._lock:
            self._capabilities[client.client_id] = tuple(client.capabilities)
            self._clients[client.client_id] = {key: getattr(client, key) for key in
                ("client_id", "runtime_id", "notebook_id", "cell_id")}

    def connect(self, editor_id: str, connection_id: str) -> None:
        with self._lock:
            self._connections.setdefault(editor_id, set()).add(connection_id)

    def disconnect(self, editor_id: str, connection_id: str) -> None:
        with self._lock:
            connections = self._connections.get(editor_id)
            if connections is not None:
                connections.discard(connection_id)
                if not connections:
                    self._connections.pop(editor_id, None)

    def bind(self, client_id: str, editor_id: str | None) -> None:
        # Called only after exclusive terminal attachment has been checked.
        with self._lock:
            if client_id not in self._clients:
                return
            previous = self._owners.get(client_id)
            if previous != editor_id:
                for action in self._actions.values():
                    if action.metadata["client_id"] == client_id and action.result is None:
                        self._finish(action, "unknown" if action.fetched else "cancelled", "recipient_replaced")
            if editor_id:
                self._owners[client_id] = editor_id
            else:
                self._owners.pop(client_id, None)
            for item in self._attention.values():
                if item["client_id"] == client_id and item["editor_id"] != (editor_id or ""):
                    item["editor_id"] = editor_id or ""
                    item["revision"] += 1
                    self._publish_attention(dict(item))

    def attention(self, client_id: str, request: dict) -> dict:
        """Accept immediately, including while the owning editor is disconnected."""
        validate_attention_request(request)
        with self._lock:
            client = self._clients.get(client_id)
            if client is None or "attention" not in self._capabilities.get(client_id, ()):
                raise EditorActionError("Client cannot publish attention", "unsupported")
            operation = request["operation"]
            attention_id = request.get("attention_id")
            item = self._attention.get(attention_id)
            if item is not None and item["client_id"] != client_id:
                raise EditorActionError("Attention belongs to another client")
            if operation == "clear":
                if item is not None:
                    self._clear_attention(item)
                return {"attention_id": attention_id, "outcome": "accepted", "reason": ""}
            if operation == "update" and item is None:
                raise EditorActionError("Attention is no longer pending", "not_found")
            if operation == "request":
                if len(self._attention) >= 256 or sum(i["client_id"] == client_id for i in self._attention.values()) >= 32:
                    raise EditorActionError("Attention capacity reached", "capacity_exceeded")
                attention_id = f"attn_{uuid.uuid4().hex}"
                item = {**client, "attention_id": attention_id, "editor_id": self._owners.get(client_id, ""),
                        "revision": 0, "state": "pending"}
                self._attention[attention_id] = item
            item.update(kind=request["kind"], message=request["message"], revision=item["revision"] + 1)
            self._publish_attention(dict(item))
            return {"attention_id": attention_id, "outcome": "accepted", "reason": ""}

    def pending_attention(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._attention.values()]

    def dismiss_attention(self, attention_id: str, editor_id: str, revision: int) -> dict:
        with self._lock:
            item = self._attention.get(attention_id)
            if item is None:
                return {"attention_id": attention_id, "dismissed": True}
            if item["editor_id"] != editor_id or not editor_id:
                raise EditorActionError("Attention belongs to another editor")
            if item["kind"] != "notice" or item["revision"] != revision:
                raise EditorActionError("Only the current notice can be dismissed")
            self._clear_attention(item)
            return {"attention_id": attention_id, "dismissed": True}

    def _clear_attention(self, item: dict) -> None:
        self._attention.pop(item["attention_id"], None)
        self._publish_attention({**item, "revision": item["revision"] + 1, "state": "cleared"})

    def submit(self, client_id: str, content: dict[str, Any]) -> dict[str, str]:
        if isinstance(content, TextSnapshot):
            snapshot = content
        else:
            validate_editor_action(content, content.get("action"))
            snapshot = TextSnapshot.capture(content)
        try:
            return self._submit(client_id, snapshot)
        finally:
            snapshot.close()

    def _submit(self, client_id: str, snapshot: TextSnapshot) -> dict[str, str]:
        content = snapshot.content
        validate_editor_action(content, content.get("action"))
        with self._lock:
            self._prune()
            client = self._clients.get(client_id)
            editor_id = self._owners.get(client_id)
            if client is None:
                raise EditorActionError("Action client is closed")
            if "editor_actions" not in self._capabilities.get(client_id, ()):
                raise EditorActionError("Client cannot submit editor actions", "unsupported")
            if not editor_id or not self._connections.get(editor_id):
                raise EditorActionError("Owning editor is not connected", "unreachable")
            pending = [a for a in self._actions.values() if a.result is None]
            if len(pending) >= 32:
                raise EditorActionError("Editor action capacity reached", "capacity_exceeded")
            metadata = {**client, "action_id": f"act_{uuid.uuid4().hex}", "editor_id": editor_id,
                        "trace_id": f"trace_{uuid.uuid4().hex}", "action": content["action"]}
            action = _Action(metadata, snapshot, time.monotonic() + self._timeout)
            self._actions[metadata["action_id"]] = action
            # Register the waiter before publishing; fast acknowledgments are safe.
            self._publish(dict(metadata))
        while True:
            action.event.wait(max(0, action.deadline - time.monotonic()))
            with self._lock:
                self._expire(action)
                if action.result is not None:
                    return dict(action.result)

    def pending(self) -> list[dict[str, str]]:
        with self._lock:
            self._prune()
            return [dict(a.metadata) for a in self._actions.values() if a.result is None]

    def fetch(self, action_id: str, editor_id: str, offset: int | None = None) -> dict[str, Any]:
        with self._lock:
            action = self._select(action_id, editor_id)
            self._expire(action)
            if action.result is not None:
                raise EditorActionError("Action is no longer pending")
            if not self._connections.get(editor_id):
                raise EditorActionError("Editor is disconnected", "unreachable")
            try:
                text, next_offset = action.content.read(offset or 0, chunked=offset is not None)
            except (ValueError, OSError) as exc:
                raise EditorActionError(str(exc), "invalid_request") from exc
            action.fetched = True
            action.complete = action.complete or next_offset == action.content.size
            if offset is not None:
                action.deadline = time.monotonic() + self._timeout
            result = {"action": dict(action.metadata), "content": {**action.content.content, "text": text},
                      "remaining_ms": max(0, int((action.deadline - time.monotonic()) * 1000))}
            if offset is not None:
                result.update(offset=offset, next_offset=next_offset, eof=next_offset == action.content.size)
            return result

    def acknowledge(self, action_id: str, editor_id: str, outcome: str) -> dict[str, str]:
        if outcome not in {"delivered", "failed"}:
            raise EditorActionError("Invalid delivery outcome", "invalid_request")
        with self._lock:
            action = self._select(action_id, editor_id)
            self._expire(action)
            if outcome == "delivered" and not action.complete:
                raise EditorActionError("Action content was not fetched")
            if action.result is not None:
                if action.result["outcome"] == outcome:
                    return dict(action.result)
                raise EditorActionError("Action already ended with another outcome")
            self._finish(action, outcome, "" if outcome == "delivered" else "frontend_delivery_failed")
            return dict(action.result)

    def close_client(self, client_id: str) -> None:
        with self._lock:
            for item in list(self._attention.values()):
                if item["client_id"] == client_id:
                    self._clear_attention(item)
            self._capabilities.pop(client_id, None)
            self._clients.pop(client_id, None)
            self._owners.pop(client_id, None)
            for action in self._actions.values():
                if action.metadata["client_id"] == client_id and action.result is None:
                    self._finish(action, "unknown" if action.fetched else "cancelled", "client_closed")

    def close(self) -> None:
        with self._lock:
            for client_id in list(self._clients):
                self.close_client(client_id)
            self._connections.clear()

    def _select(self, action_id: str, editor_id: str) -> _Action:
        action = self._actions.get(action_id)
        if action is None:
            raise EditorActionError("Unknown or expired action", "not_found")
        if action.metadata["editor_id"] != editor_id:
            raise EditorActionError("Action belongs to another editor")
        return action

    def _finish(self, action: _Action, outcome: str, reason: str) -> None:
        action.result = {"action_id": action.metadata["action_id"], "outcome": outcome, "reason": reason}
        action.content.close()
        action.event.set()

    def _expire(self, action: _Action) -> None:
        if action.result is None and time.monotonic() >= action.deadline:
            self._finish(action, "unknown" if action.fetched else "failed", "timeout")

    def _prune(self) -> None:
        for action in self._actions.values():
            self._expire(action)
        completed = [key for key, a in self._actions.items() if a.result is not None]
        for key in completed[:-128]:
            del self._actions[key]
