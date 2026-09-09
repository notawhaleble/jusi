from __future__ import annotations

import os
import socket
import tempfile
import threading
from typing import Callable

from jusi.application.editor_actions import EditorActionError
from jusi.infrastructure.plugin_worker_channel import read_frame, write_frame
from jusi.protocol import ProtocolValidationError, validate_application_action


class _Endpoint:
    def __init__(self, submit: Callable) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="jusi-action-", dir="/tmp")
        self.path = self.directory.name + "/socket"
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.bind(self.path)
            os.chmod(self.path, 0o600)
            self.socket.listen(8)
            self.socket.settimeout(.2)
        except OSError:
            self.socket.close()
            self.directory.cleanup()
            raise
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(32)
        self.connections: set[socket.socket] = set()
        self.lock = threading.Lock()
        self.submit = submit
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            with self.lock:
                self.connections.add(connection)
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        try:
            with connection:
                connection.settimeout(5)
                with connection.makefile("rwb") as stream:
                    request = validate_application_action(read_frame(stream))
                    if request["kind"] != "application.editor_action":
                        raise ProtocolValidationError("Expected application action request")
                    try:
                        result = self.submit(request["content"])
                    except (EditorActionError, ProtocolValidationError) as exc:
                        result = {"action_id": "", "outcome": "failed", "reason": getattr(exc, "reason", "invalid_request")}
                    write_frame(stream, {"protocol_version": 1, "kind": "application.action_result",
                                         "request_id": request["request_id"], **result})
        except (OSError, ValueError, EOFError):
            pass
        finally:
            with self.lock:
                self.connections.discard(connection)
            self.slots.release()

    def close(self) -> None:
        self.stopped.set()
        self.socket.close()
        self.thread.join(timeout=1)
        with self.lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        self.directory.cleanup()


class LocalEditorActionBroker:
    """Private target-side application IPC; never a frontend-local proxy."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._endpoints: dict[str, _Endpoint] = {}

    def open(self, client_id: str, submit: Callable) -> dict[str, str]:
        with self._lock:
            endpoint = _Endpoint(submit)
            self._endpoints[client_id] = endpoint
            return {"JUSI_EDITOR_ACTION_SOCKET": endpoint.path}

    def close_client(self, client_id: str) -> None:
        with self._lock:
            endpoint = self._endpoints.pop(client_id, None)
        if endpoint is not None:
            endpoint.close()

    def close(self) -> None:
        with self._lock:
            clients = list(self._endpoints)
        for client_id in clients:
            self.close_client(client_id)
