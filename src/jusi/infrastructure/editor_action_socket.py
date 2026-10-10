from __future__ import annotations

import os
import select
import socket
import tempfile
import threading
from typing import Callable

from jusi.application.editor_actions import EditorActionError
from jusi.application.text_snapshot import TextSnapshot
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
            self.socket.setblocking(False)
            self.wakeup_reader, self.wakeup_writer = socket.socketpair()
        except OSError:
            self.socket.close()
            self.directory.cleanup()
            raise
        self.stopped = threading.Event()
        self.close_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(32)
        self.connections: set[socket.socket] = set()
        self.lock = threading.Lock()
        self.submit = submit
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while not self.stopped.is_set():
            try:
                readable, _, _ = select.select([self.socket, self.wakeup_reader], [], [])
                if self.wakeup_reader in readable or self.stopped.is_set():
                    return
                connection, _ = self.socket.accept()
            except BlockingIOError:
                continue
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            with self.lock:
                if self.stopped.is_set():
                    connection.close()
                    self.slots.release()
                    return
                self.connections.add(connection)
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection: socket.socket) -> None:
        try:
            with connection:
                connection.settimeout(5)
                with connection.makefile("rwb") as stream:
                    request = validate_application_action(read_frame(stream))
                    if request["kind"] not in {"application.editor_action", "application.editor_action_begin", "application.attention"}:
                        raise ProtocolValidationError("Expected application action request")
                    snapshot = None
                    attention = request["kind"] == "application.attention"
                    try:
                        content = request["content"]
                        if request["kind"] == "application.editor_action_begin":
                            snapshot = TextSnapshot(content)
                            while True:
                                chunk = validate_application_action(read_frame(stream))
                                if chunk["kind"] != "application.editor_action_chunk" or chunk["request_id"] != request["request_id"]:
                                    raise ProtocolValidationError("Invalid application text stream identity")
                                snapshot.append(chunk["text"])
                                if chunk["eof"]:
                                    break
                            content = snapshot
                        result = self.submit(content)
                    except (EditorActionError, ValueError, OSError) as exc:
                        result = {"attention_id" if attention else "action_id": "", "outcome": "failed", "reason": getattr(exc, "reason", "invalid_request")}
                    finally:
                        if snapshot is not None:
                            snapshot.close()
                    write_frame(stream, {"protocol_version": 1, "kind": "application.attention_result" if attention else "application.action_result",
                                         "request_id": request["request_id"], **result})
        except (OSError, ValueError, EOFError):
            pass
        finally:
            with self.lock:
                self.connections.discard(connection)
            self.slots.release()

    def close(self) -> None:
        with self.close_lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
            # Closing a listening socket in another thread does not wake accept
            # on every platform. EOF on this private socket wakes select directly.
            self.wakeup_writer.close()
            self.thread.join(timeout=1)
            self.socket.close()
            self.wakeup_reader.close()
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
