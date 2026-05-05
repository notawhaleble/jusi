from __future__ import annotations

import base64
import json
import os
import pickle
import signal
import threading
from queue import Empty
from dataclasses import dataclass, field
from itertools import count
from types import SimpleNamespace
from typing import Any, Protocol
from uuid import uuid4

from jusi.domain.models import CellExecution, ClientTransport, ExecutableCell, HandlerHandoff, Session, SessionTarget, parse_handler_handoff_payload
from jusi.infrastructure.client_view import build_client_view
from jusi.infrastructure.client_runtime import InProcessTranscriptHandle
from jusi.infrastructure.debug_timing import emit_timing
from jusi.plugins import build_display_handler_registry, collect_kernel_extension_modules
from jusi.visidata_support import JUSI_VISIDATARC_ENV


class RuntimeDependencyError(RuntimeError):
    """Raised when an optional runtime dependency is required but unavailable."""


MANAGED_IOPUB_POLL_TIMEOUT_SECONDS = 0.01
JUSI_SESSION_CONFIG_ENV = "JUSI_SESSION_CONFIG_JSON"
JUSI_KERNEL_EXTENSIONS_ENV = "JUSI_KERNEL_EXTENSION_MODULES_JSON"


def _connection_registry_path(connection_file: str) -> str:
    return f"{connection_file}.jusi-attached.json"


def _is_live_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _load_attached_record(connection_file: str) -> dict[str, object]:
    path = _connection_registry_path(connection_file)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return {"pids": [], "expires_at": None}
    except json.JSONDecodeError:
        return {"pids": [], "expires_at": None}
    if isinstance(raw, list):
        raw = {"pids": raw, "expires_at": None}
    if not isinstance(raw, dict):
        return {"pids": [], "expires_at": None}
    pids: list[int] = []
    for value in raw.get("pids", []):
        try:
            pid = int(value)
        except (TypeError, ValueError):
            continue
        if _is_live_pid(pid):
            pids.append(pid)
    expires_at = raw.get("expires_at")
    try:
        parsed_expires_at = float(expires_at) if expires_at is not None else None
    except (TypeError, ValueError):
        parsed_expires_at = None
    return {"pids": sorted(set(pids)), "expires_at": parsed_expires_at}


def _store_attached_record(connection_file: str, *, pids: list[int], expires_at: float | None) -> None:
    path = _connection_registry_path(connection_file)
    live_pids = [pid for pid in sorted(set(pids)) if _is_live_pid(pid)]
    if not live_pids and expires_at is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"pids": live_pids, "expires_at": expires_at}, handle)


def _register_attached_supervisor(connection_file: str, pid: int) -> None:
    if pid <= 0:
        return
    record = _load_attached_record(connection_file)
    _store_attached_record(
        connection_file,
        pids=list(record["pids"]) + [pid],
        expires_at=record["expires_at"] if isinstance(record.get("expires_at"), (int, float)) else None,
    )


def _unregister_attached_supervisor(connection_file: str, pid: int) -> None:
    if pid <= 0:
        return
    record = _load_attached_record(connection_file)
    _store_attached_record(
        connection_file,
        pids=[known_pid for known_pid in list(record["pids"]) if known_pid != pid],
        expires_at=record["expires_at"] if isinstance(record.get("expires_at"), (int, float)) else None,
    )


def _set_attached_expiry(connection_file: str, expires_at: float | None) -> None:
    record = _load_attached_record(connection_file)
    _store_attached_record(connection_file, pids=list(record["pids"]), expires_at=expires_at)


def _get_attached_expiry(connection_file: str) -> float | None:
    record = _load_attached_record(connection_file)
    expires_at = record.get("expires_at")
    return float(expires_at) if isinstance(expires_at, (int, float)) else None


def _signal_attached_supervisors(connection_file: str, *, sig: int, exclude_pid: int) -> None:
    live_peers: list[int] = []
    record = _load_attached_record(connection_file)
    expires_at = record["expires_at"] if isinstance(record.get("expires_at"), (int, float)) else None
    for pid in list(record["pids"]):
        if pid == exclude_pid:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except PermissionError:
            live_peers.append(pid)
            continue
        live_peers.append(pid)
    _store_attached_record(
        connection_file,
        pids=live_peers + ([exclude_pid] if _is_live_pid(exclude_pid) else []),
        expires_at=expires_at,
    )


class RuntimeClientHandle(Protocol):
    def bind(self, client_bufnr: int) -> None:
        ...

    def activate(self, cell_id: int) -> None:
        ...

    def update_execution_status(self, status: str) -> None:
        ...

    def append_execution_event(self, event: dict) -> None:
        ...

    def set_transport(self, transport: dict) -> None:
        ...

    def read_view(self) -> dict:
        ...

    def shutdown(self, reason: str) -> None:
        ...

    def plugin_runtime_is_alive(self) -> bool | None:
        ...


@dataclass
class PendingInputRequest:
    client_id: str
    cell_id: int
    msg_id: str


@dataclass
class InMemoryClientHandle:
    client_id: str
    notebook_id: str
    session_id: str
    client_bufnr: int = -1
    active_cell_id: int | None = None
    execution_status: str = ""
    shutdown_reason: str = ""
    view_revision: int = 0
    lifecycle: list[str] = field(default_factory=list)
    transcript: list[dict] = field(default_factory=list)
    transport: dict = field(default_factory=dict)

    def bind(self, client_bufnr: int) -> None:
        self.client_bufnr = client_bufnr
        self.view_revision += 1
        self.lifecycle.append(f"bind:{client_bufnr}")

    def activate(self, cell_id: int) -> None:
        self.active_cell_id = cell_id
        self.view_revision += 1
        self.lifecycle.append(f"activate:{cell_id}")

    def update_execution_status(self, status: str) -> None:
        if self.execution_status == status:
            return
        self.execution_status = status
        self.view_revision += 1
        self.lifecycle.append(f"status:{status}")

    def append_execution_event(self, event: dict) -> None:
        self.transcript.append(dict(event))
        self.view_revision += 1
        event_type = str(event.get("type", "")).strip() or "event"
        self.lifecycle.append(f"event:{event_type}")

    def read_view(self) -> dict:
        view = build_client_view(
            client_id=self.client_id,
            session_id=self.session_id,
            client_bufnr=self.client_bufnr,
            active_cell_id=self.active_cell_id,
            execution_status=self.execution_status,
            transcript=self.transcript,
        )
        view["revision"] = self.view_revision
        if self.transport:
            view["transport"] = dict(self.transport)
        return view

    def set_transport(self, transport: dict) -> None:
        self.transport = dict(transport)
        self.view_revision += 1
        self.lifecycle.append(f"transport:{self.transport.get('kind', '')}")

    def shutdown(self, reason: str) -> None:
        self.shutdown_reason = reason
        self.client_bufnr = -1
        self.view_revision += 1
        self.lifecycle.append(f"shutdown:{reason}")

    def plugin_runtime_is_alive(self) -> bool | None:
        return None


@dataclass
class ManagedClientHandle(InProcessTranscriptHandle):
    runtime_kind: str = "managed"


@dataclass
class RuntimeClient:
    client_id: str
    notebook_id: str
    session_id: str
    handle: RuntimeClientHandle
    state: str = "active"
    client_bufnr: int = -1
    cell_id: int | None = None
    shutdown_reason: str = ""
    transport: ClientTransport = field(default_factory=ClientTransport)


@dataclass
class RuntimeSessionClients:
    clients: dict[str, RuntimeClient] = field(default_factory=dict)


class ClientRegistryRuntime:
    def __init__(self) -> None:
        self._client_counter = count(1)
        self._session_clients: dict[str, RuntimeSessionClients] = {}

    def start_client(self, session: Session, notebook_id: str, cell_id: int, initial_status: str) -> str:
        client_id = self.prepare_client(notebook_id, session.session_id)
        self.activate_client(session, client_id, cell_id)
        self.update_client_execution_status(session, client_id, initial_status)
        return client_id

    def prepare_client(self, notebook_id: str, session_id: str) -> str:
        session_clients = self._ensure_session_clients(session_id)
        client_id = self._next_client_id(session_id)
        session_clients.clients[client_id] = RuntimeClient(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
            handle=self._build_client_handle(client_id, notebook_id, session_id),
        )
        return client_id

    def activate_client(self, session: Session, client_id: str, cell_id: int) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        runtime_client.handle.activate(cell_id)
        runtime_client.state = "active"
        runtime_client.cell_id = cell_id

    def bind_prepared_client(self, session: Session, client_id: str, client_bufnr: int) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        runtime_client.handle.bind(client_bufnr)
        runtime_client.client_bufnr = client_bufnr

    def update_client_execution_status(self, session: Session, client_id: str, status: str) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        runtime_client.handle.update_execution_status(status)

    def append_client_execution_event(self, session: Session, client_id: str, event: dict) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        runtime_client.handle.append_execution_event(event)

    def read_client_view(self, session: Session, client_id: str) -> dict:
        runtime_client = self._require_client(session.session_id, client_id)
        view = runtime_client.handle.read_view()
        if runtime_client.transport.kind:
            view["transport"] = {
                "kind": runtime_client.transport.kind,
                "attach_cmd": list(runtime_client.transport.attach_cmd),
                "attach_env": dict(runtime_client.transport.attach_env),
                "session_id": runtime_client.transport.session_id,
                "client_id": runtime_client.transport.client_id,
                "handler_id": runtime_client.transport.handler_id,
            }
        return view

    def set_client_transport(self, session: Session, client_id: str, transport: ClientTransport) -> None:
        runtime_client = self._require_client(session.session_id, client_id)
        transport_env = dict(transport.attach_env)
        if session.visidatarc_content:
            transport_env[JUSI_VISIDATARC_ENV] = session.visidatarc_content
        status_path = getattr(runtime_client.handle, "status_path", "")
        if status_path:
            transport_env["JUSI_CLIENT_STATUS_FILE"] = status_path
        if transport.handler_id:
            pid_path = getattr(runtime_client.handle, "plugin_runtime_pid_path", "")
            if pid_path:
                transport_env["JUSI_PLUGIN_RUNTIME_PID_FILE"] = pid_path
            socket_path = getattr(runtime_client.handle, "plugin_runtime_socket_path", "")
            if socket_path:
                transport_env["JUSI_PLUGIN_RUNTIME_CONTROL_SOCKET"] = socket_path
            actions_path = getattr(runtime_client.handle, "plugin_frontend_actions_path", "")
            if actions_path:
                transport_env["JUSI_PLUGIN_FRONTEND_ACTIONS_FILE"] = actions_path
        transport = ClientTransport(
            kind=transport.kind,
            attach_cmd=list(transport.attach_cmd),
            attach_env=transport_env,
            session_id=transport.session_id,
            client_id=transport.client_id,
            handler_id=transport.handler_id,
        )
        runtime_client.transport = transport
        runtime_client.handle.set_transport(
            {
                "kind": transport.kind,
                "attach_cmd": list(transport.attach_cmd),
                "attach_env": dict(transport.attach_env),
                "session_id": transport.session_id,
                "client_id": transport.client_id,
                "handler_id": transport.handler_id,
            }
        )

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        session_clients = self._session_clients.get(session.session_id)
        if session_clients is None:
            return
        runtime_client = session_clients.clients.get(client_id)
        if runtime_client is None:
            return
        runtime_client.handle.shutdown(reason)
        runtime_client.state = "shutdown"
        runtime_client.shutdown_reason = reason
        runtime_client.client_bufnr = -1
        session_clients.clients.pop(client_id, None)

    def request_plugin_runtime(self, session: Session, client_id: str, payload: dict[str, object]) -> dict[str, object]:
        runtime_client = self._require_client(session.session_id, client_id)
        request = getattr(runtime_client.handle, "request_plugin_runtime", None)
        if not callable(request):
            raise RuntimeError("runtime client does not support plugin runtime control")
        response = request(dict(payload))
        if isinstance(response, dict):
            return dict(response)
        return {}

    def release_session_clients(self, session_id: str, reason: str) -> None:
        session_clients = self._session_clients.pop(session_id, None)
        if session_clients is None:
            return
        for runtime_client in session_clients.clients.values():
            runtime_client.handle.shutdown(reason)
            runtime_client.state = "shutdown"
            runtime_client.shutdown_reason = reason
            runtime_client.client_bufnr = -1

    def get_client(self, session_id: str, client_id: str) -> RuntimeClient | None:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None:
            return None
        return session_clients.clients.get(client_id)

    def list_clients(self, session_id: str) -> list[RuntimeClient]:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None:
            return []
        return list(session_clients.clients.values())

    def close(self) -> None:
        for session_id in list(self._session_clients.keys()):
            self.release_session_clients(session_id, reason="backend_shutdown")

    def sync_disconnect_deadline(self, session: Session, expires_at: float | None) -> float | None:
        _ = session
        return expires_at

    def expire_session(self, session: Session) -> None:
        self.stop_session(session)

    def _next_client_id(self, session_id: str) -> str:
        _ = session_id
        ident = next(self._client_counter)
        return f"client-{ident}"

    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str) -> RuntimeClientHandle:
        return InMemoryClientHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )

    def _ensure_session_clients(self, session_id: str) -> RuntimeSessionClients:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None:
            session_clients = RuntimeSessionClients()
            self._session_clients[session_id] = session_clients
        return session_clients

    def _require_client(self, session_id: str, client_id: str) -> RuntimeClient:
        session_clients = self._session_clients.get(session_id)
        if session_clients is None or client_id not in session_clients.clients:
            raise ValueError("Runtime client id does not match current session state")
        return session_clients.clients[client_id]


def _jusi_kernel_env(
    extension_modules: list[str] | tuple[str, ...],
    session_config: dict[str, object] | None = None,
) -> dict[str, str]:
    return {
        JUSI_SESSION_CONFIG_ENV: json.dumps(dict(session_config or {})),
        JUSI_KERNEL_EXTENSIONS_ENV: json.dumps(list(extension_modules)),
    }


def _start_new_kernel(
    kernel_name: str,
    *,
    env: dict[str, str] | None = None,
    extra_arguments: list[str] | None = None,
) -> tuple[Any, Any]:
    try:
        from jupyter_client.manager import start_new_kernel
        from jupyter_client.kernelspec import NoSuchKernel
    except ModuleNotFoundError as exc:
        raise RuntimeDependencyError(
            "Managed runtime requires jupyter_client to be installed"
        ) from exc

    try:
        kwargs: dict[str, Any] = {"kernel_name": kernel_name}
        if env:
            merged_env = os.environ.copy()
            merged_env.update(env)
            kwargs["env"] = merged_env
        if extra_arguments:
            kwargs["extra_arguments"] = list(extra_arguments)
        return start_new_kernel(**kwargs)
    except NoSuchKernel as exc:
        raise RuntimeDependencyError(
            "Managed runtime requires an installed kernelspec for "
            f"'{kernel_name}'. Install ipykernel in the active environment."
        ) from exc


def _effective_target_kernel_name(target: SessionTarget, kernel_name: str) -> str:
    requested = kernel_name.strip() or "python3"
    configured = str(target.config.get("kernel_name", "")).strip()
    if configured:
        return configured
    if target.kind == "venv":
        return "python3"
    return requested


def _attach_existing_kernel(connection_file: str) -> Any:
    try:
        from jupyter_client import BlockingKernelClient
    except ModuleNotFoundError as exc:
        raise RuntimeDependencyError(
            "Managed runtime requires jupyter_client to be installed"
        ) from exc

    client = BlockingKernelClient()
    client.load_connection_file(connection_file=connection_file)
    client.start_channels()
    return client


class InMemoryKernelRuntime(ClientRegistryRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._session_counter = count(1)
        self._handoffs: dict[tuple[str, str], HandlerHandoff] = {}

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        ident = next(self._session_counter)
        session_id = f"sess-{uuid4().hex}"
        connection = f"inmemory://{kernel_name}/{ident}"
        return session_id, connection

    def start_target(self, target: SessionTarget, kernel_name: str) -> tuple[str, str]:
        session_id, connection = self.start_managed(_effective_target_kernel_name(target, kernel_name))
        return session_id, connection

    def attach_target(self, target: SessionTarget) -> tuple[str, str]:
        if target.kind != "connection_file":
            raise RuntimeError(f"Attach target kind is not supported yet: {target.kind or 'external'}")
        ident = next(self._session_counter)
        session_id = f"sess-{uuid4().hex}"
        connection = target.value or f"{target.kind or 'external'}://attached/{ident}"
        return session_id, connection

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        _ = (session, cell, client)
        self.append_client_execution_event(
            session,
            client.client_id,
            {"type": "execution_started", "cell_id": client.cell_id, "kind": cell.kind, "syntax": cell.syntax},
        )
        if cell.keep_running:
            self.update_client_execution_status(session, client.client_id, "busy")
            self.append_client_execution_event(
                session,
                client.client_id,
                {"type": "execution_state", "status": "busy"},
            )
            return "busy"
        if cell.kind == "magic":
            handoff = self._synthetic_handoff_for_magic(cell)
            if handoff is not None:
                self._handoffs[(session.session_id, client.client_id)] = handoff
                self.append_client_execution_event(
                    session,
                    client.client_id,
                    {
                        "type": "handler_handoff",
                        "handler_id": handoff.handler_id,
                        "magic_name": handoff.magic_name,
                        "content": handoff.content,
                        "meta": dict(handoff.meta),
                    },
                )
            self.update_client_execution_status(session, client.client_id, "follow-up")
            self.append_client_execution_event(
                session,
                client.client_id,
                {"type": "execution_finished", "status": "follow-up"},
            )
            return "follow-up"
        self.update_client_execution_status(session, client.client_id, "done")
        self.append_client_execution_event(
            session,
            client.client_id,
            {"type": "execution_finished", "status": "done"},
        )
        return "done"

    @staticmethod
    def _synthetic_handoff_for_magic(cell: ExecutableCell) -> HandlerHandoff | None:
        if not cell.main_lines:
            return None
        first_line = str(cell.main_lines[0]).strip()
        if not first_line.startswith("%%"):
            return None
        magic_name = first_line[2:].split(None, 1)[0].strip()
        if not magic_name:
            return None
        content = "\n".join(cell.main_lines[1:])
        meta: dict[str, object] = {"source": "inmemory"}
        if magic_name == "vd":
            content = base64.b64encode(pickle.dumps(content)).decode("ascii")
        return HandlerHandoff(
            handler_id=magic_name,
            magic_name=magic_name,
            content=content,
            meta=meta,
        )

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        _ = (session, execution)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def disconnect_session(self, session: Session, reason: str) -> None:
        self.release_session_clients(session.session_id, reason=reason)

    def sync_disconnect_deadline(self, session: Session, expires_at: float | None) -> float | None:
        connection_file = str(getattr(session, "connection", "") or "")
        if session.target.kind != "connection_file" or not connection_file:
            return expires_at
        if expires_at is not None:
            _set_attached_expiry(connection_file, expires_at)
            return expires_at
        shared = _get_attached_expiry(connection_file)
        if shared is not None:
            return shared
        _set_attached_expiry(connection_file, None)
        return None

    def expire_session(self, session: Session) -> None:
        self.stop_session(session)

    def sync_disconnect_deadline(self, session: Session, expires_at: float | None) -> float | None:
        connection_file = str(getattr(session, "connection", "") or "")
        if session.target.kind != "connection_file" or not connection_file:
            return expires_at
        if expires_at is not None:
            _set_attached_expiry(connection_file, expires_at)
            return expires_at
        shared = _get_attached_expiry(connection_file)
        if shared is not None:
            return shared
        _set_attached_expiry(connection_file, None)
        return None

    def expire_session(self, session: Session) -> None:
        self.stop_session(session)

    def stop_session(self, session: Session) -> None:
        self.release_session_clients(session.session_id, reason="session_stop")

    def consume_handler_handoff(self, session: Session, client_id: str) -> HandlerHandoff | None:
        return self._handoffs.pop((session.session_id, client_id), None)


class ManagedKernelRuntime(ClientRegistryRuntime):
    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, Any] = {}

    @staticmethod
    def _session_execute_lock(runtime_session: Any) -> threading.RLock:
        lock = getattr(runtime_session, "execute_lock", None)
        if lock is not None and hasattr(lock, "acquire") and hasattr(lock, "release"):
            return lock
        lock = threading.RLock()
        runtime_session.execute_lock = lock
        return lock

    def supports_background_execute(self) -> bool:
        return True

    def supports_background_stop(self) -> bool:
        return True

    def start_target(self, target: SessionTarget, kernel_name: str) -> tuple[str, str]:
        effective_kernel_name = _effective_target_kernel_name(target, kernel_name)
        extension_modules = collect_kernel_extension_modules(build_display_handler_registry())
        kernel_env = _jusi_kernel_env(extension_modules, target.config)
        km, kc = _start_new_kernel(
            kernel_name=effective_kernel_name,
            env=kernel_env,
            extra_arguments=["--ext", "jusi.kernel"],
        )
        session_id = f"sess-{uuid4().hex}"
        self._sessions[session_id] = SimpleNamespace(
            manager=km,
            client=kc,
            interrupted_client_ids=set(),
            pending_inputs={},
            handoffs={},
            execute_lock=threading.RLock(),
            jusi_magics_registered=True,
        )
        return session_id, str(getattr(km, "connection_file", ""))

    def attach_target(self, target: SessionTarget) -> tuple[str, str]:
        if target.kind != "connection_file":
            raise RuntimeError(f"Attach target kind is not supported yet: {target.kind or 'external'}")
        connection_file = target.value.strip()
        if not connection_file:
            raise RuntimeError("Attach target requires a connection file")
        client = _attach_existing_kernel(connection_file)
        session_id = f"sess-{uuid4().hex}"
        _register_attached_supervisor(connection_file, os.getpid())
        self._sessions[session_id] = SimpleNamespace(
            manager=None,
            client=client,
            interrupted_client_ids=set(),
            pending_inputs={},
            handoffs={},
            execute_lock=threading.RLock(),
            external=True,
            connection_file=connection_file,
        )
        return session_id, connection_file

    def start_managed(self, kernel_name: str) -> tuple[str, str]:
        km, kc = _start_new_kernel(kernel_name=kernel_name)
        session_id = f"sess-{uuid4().hex}"
        self._sessions[session_id] = SimpleNamespace(
            manager=km,
            client=kc,
            interrupted_client_ids=set(),
            pending_inputs={},
            handoffs={},
            execute_lock=threading.RLock(),
            jusi_magics_registered=False,
        )
        return session_id, str(getattr(km, "connection_file", ""))

    def _build_client_handle(self, client_id: str, notebook_id: str, session_id: str) -> RuntimeClientHandle:
        return ManagedClientHandle(
            client_id=client_id,
            notebook_id=notebook_id,
            session_id=session_id,
        )

    def execute_cell(self, session: Session, cell: ExecutableCell, client: CellExecution) -> str:
        runtime_session = self._require_session(session.session_id)
        code = "\n".join(cell.main_lines)
        emit_timing(
            "runtime.managed.execute_cell.start",
            session_id=session.session_id,
            client_id=client.client_id,
            cell_id=client.cell_id,
            kind=cell.kind,
            syntax=cell.syntax,
        )
        self.append_client_execution_event(
            session,
            client.client_id,
            {"type": "execution_started", "cell_id": client.cell_id, "kind": cell.kind, "syntax": cell.syntax},
        )
        with self._session_execute_lock(runtime_session):
            emit_timing(
                "runtime.managed.execute_lock.acquired",
                session_id=session.session_id,
                client_id=client.client_id,
                cell_id=client.cell_id,
            )
            if cell.keep_running:
                runtime_session.client.execute(code)
                emit_timing(
                    "runtime.managed.execute_cell.sent",
                    session_id=session.session_id,
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    keep_running=True,
                )
                self.update_client_execution_status(session, client.client_id, "busy")
                self.append_client_execution_event(
                    session,
                    client.client_id,
                    {"type": "execution_state", "status": "busy"},
                )
                return "busy"
            if cell.kind == "magic":
                self._ensure_jusi_magics(runtime_session, session.target.config)
                msg_id = runtime_session.client.execute(code, store_history=False)
                emit_timing(
                    "runtime.managed.execute_cell.sent",
                    session_id=session.session_id,
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                    kind=cell.kind,
                )
                return self._drive_execution(runtime_session, session, client, msg_id)

            msg_id = runtime_session.client.execute(code)
            emit_timing(
                "runtime.managed.execute_cell.sent",
                session_id=session.session_id,
                client_id=client.client_id,
                cell_id=client.cell_id,
                msg_id=msg_id,
                kind=cell.kind,
            )
            return self._drive_execution(runtime_session, session, client, msg_id)

    def _ensure_jusi_magics(self, runtime_session: Any, session_config: dict[str, object] | None = None) -> None:
        if bool(getattr(runtime_session, "jusi_magics_registered", False)):
            return
        if bool(getattr(runtime_session, "external", False)):
            raise RuntimeError(
                "Plugin magics on attached kernels require the 'jusi.kernel' IPython extension to be preloaded"
            )
        raise RuntimeError("Jusi kernel magics were not preloaded for this managed session")

    def consume_handler_handoff(self, session: Session, client_id: str) -> HandlerHandoff | None:
        runtime_session = self._require_session(session.session_id)
        handoffs = getattr(runtime_session, "handoffs", None)
        if not isinstance(handoffs, dict):
            return None
        handoff = handoffs.pop(client_id, None)
        if isinstance(handoff, HandlerHandoff):
            return handoff
        return None

    def reply_input(self, session: Session, execution: CellExecution, value: str) -> str:
        runtime_session = self._require_session(session.session_id)
        with self._session_execute_lock(runtime_session):
            pending_input = runtime_session.pending_inputs.get(execution.client_id)
            if pending_input is None or pending_input.cell_id != execution.cell_id:
                raise ValueError("No pending input_request for the tracked cell client")
            reply_input = getattr(runtime_session.client, "input", None)
            if not callable(reply_input):
                raise RuntimeError("Managed runtime client does not support stdin replies")
            reply_input(value)
            runtime_session.pending_inputs.pop(execution.client_id, None)
            return self._drive_execution(runtime_session, session, execution, pending_input.msg_id)

    def _drive_execution(self, runtime_session: Any, session: Session, client: CellExecution, msg_id: str) -> str:
        status = client.status if client.status in {"error", "follow-up", "interrupted"} else "done"
        saw_iopub = False
        saw_stream = False
        while True:
            stdin_message = self._try_get_stdin_request(runtime_session.client, msg_id)
            if stdin_message is not None:
                runtime_session.pending_inputs[client.client_id] = PendingInputRequest(
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                )
                self.append_client_execution_event(
                    session,
                    client.client_id,
                    {
                        "type": "input_request",
                        "prompt": str(stdin_message.get("content", {}).get("prompt", "")),
                        "password": bool(stdin_message.get("content", {}).get("password", False)),
                    },
                )
                emit_timing(
                    "runtime.managed.stdin_request",
                    session_id=session.session_id,
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                )
                return "busy"

            message = self._try_get_iopub_message(runtime_session.client)
            if message is None:
                continue
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            msg_type = str(message.get("msg_type", "")).strip()
            emit_timing(
                "runtime.managed.iopub",
                session_id=session.session_id,
                client_id=client.client_id,
                cell_id=client.cell_id,
                msg_id=msg_id,
                msg_type=msg_type,
            )
            if not saw_iopub:
                saw_iopub = True
                emit_timing(
                    "runtime.managed.first_iopub",
                    session_id=session.session_id,
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                    msg_type=msg_type,
                )
            if msg_type == "stream" and not saw_stream:
                saw_stream = True
                emit_timing(
                    "runtime.managed.first_stream",
                    session_id=session.session_id,
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                )
            result = self._handle_iopub_message(runtime_session, session, client.client_id, message, status)
            if result is None:
                continue
            status = result
            if msg_type == "status" and message.get("content", {}).get("execution_state") == "idle":
                emit_timing(
                    "runtime.managed.idle",
                    session_id=session.session_id,
                    client_id=client.client_id,
                    cell_id=client.cell_id,
                    msg_id=msg_id,
                    final_status=status,
                )
                runtime_session.pending_inputs.pop(client.client_id, None)
                return status

    def _handle_iopub_message(self, runtime_session: Any, session: Session, client_id: str, message: dict, status: str) -> str | None:
        msg_type = message.get("msg_type", "")
        handoff = parse_handler_handoff_payload(
            message.get("content", {}).get("data", {}),
            metadata=message.get("content", {}).get("metadata", {}),
        )
        if handoff is not None:
            handoffs = getattr(runtime_session, "handoffs", None)
            if not isinstance(handoffs, dict):
                handoffs = {}
                runtime_session.handoffs = handoffs
            handoffs[client_id] = handoff
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "handler_handoff",
                    "handler_id": handoff.handler_id,
                    "magic_name": handoff.magic_name,
                    "content": handoff.content,
                    "meta": dict(handoff.meta),
                },
            )
            emit_timing(
                "runtime.managed.handoff",
                session_id=session.session_id,
                client_id=client_id,
                handler_id=handoff.handler_id,
                magic_name=handoff.magic_name,
            )
            return "follow-up"
        if msg_type == "execute_input":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "execute_input",
                    "execution_count": message.get("content", {}).get("execution_count"),
                    "code": str(message.get("content", {}).get("code", "")),
                },
            )
            return None
        if msg_type == "stream":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "stream",
                    "name": message.get("content", {}).get("name", ""),
                    "text": message.get("content", {}).get("text", ""),
                },
            )
            emit_timing(
                "runtime.managed.stream",
                session_id=session.session_id,
                client_id=client_id,
                name=str(message.get("content", {}).get("name", "")),
                text_len=len(str(message.get("content", {}).get("text", ""))),
            )
            return None
        if msg_type == "error":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "error",
                    "ename": message.get("content", {}).get("ename", ""),
                    "evalue": message.get("content", {}).get("evalue", ""),
                    "traceback": list(message.get("content", {}).get("traceback", [])),
                },
            )
            return "error"
        if msg_type == "display_data":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "display_data",
                    "data": dict(message.get("content", {}).get("data", {})),
                    "display_id": str(message.get("content", {}).get("transient", {}).get("display_id", "")),
                },
            )
            return None
        if msg_type == "update_display_data":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "update_display_data",
                    "data": dict(message.get("content", {}).get("data", {})),
                    "display_id": str(message.get("content", {}).get("transient", {}).get("display_id", "")),
                },
            )
            return None
        if msg_type == "execute_result":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "execute_result",
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "clear_output":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "clear_output",
                    "wait": bool(message.get("content", {}).get("wait", False)),
                },
            )
            return None
        if msg_type == "comm_open":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "comm_open",
                    "comm_id": str(message.get("content", {}).get("comm_id", "")),
                    "target_name": str(message.get("content", {}).get("target_name", "")),
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "comm_msg":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "comm_msg",
                    "comm_id": str(message.get("content", {}).get("comm_id", "")),
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "comm_close":
            self.append_client_execution_event(
                session,
                client_id,
                {
                    "type": "comm_close",
                    "comm_id": str(message.get("content", {}).get("comm_id", "")),
                    "data": dict(message.get("content", {}).get("data", {})),
                },
            )
            return None
        if msg_type == "status" and message.get("content", {}).get("execution_state") == "idle":
            if client_id in getattr(runtime_session, "interrupted_client_ids", set()):
                status = "interrupted"
                runtime_session.interrupted_client_ids.discard(client_id)
            self.update_client_execution_status(session, client_id, status)
            self.append_client_execution_event(
                session,
                client_id,
                {"type": "execution_finished", "status": status},
            )
            return status
        return None

    def _try_get_iopub_message(self, kernel_client: Any) -> dict | None:
        try:
            return kernel_client.get_iopub_msg(timeout=MANAGED_IOPUB_POLL_TIMEOUT_SECONDS)
        except Empty:
            return None

    def _drive_export_execution(self, kernel_client: Any, msg_id: str) -> str:
        while True:
            message = self._try_get_iopub_message(kernel_client)
            if message is None:
                continue
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            msg_type = message.get("msg_type", "")
            if msg_type == "error":
                content = message.get("content", {})
                return f"Jusi kernel bootstrap failed: {content.get('ename', '')}: {content.get('evalue', '')}"
            if msg_type == "status" and message.get("content", {}).get("execution_state") == "idle":
                return ""

    def _try_get_stdin_request(self, kernel_client: Any, msg_id: str) -> dict | None:
        get_stdin_msg = getattr(kernel_client, "get_stdin_msg", None)
        if get_stdin_msg is None:
            return None
        try:
            message = get_stdin_msg(timeout=0.0)
        except Empty:
            return None
        if message.get("parent_header", {}).get("msg_id") != msg_id:
            return None
        if message.get("msg_type", "") != "input_request":
            return None
        return message

    def interrupt_kernel(self, session: Session, execution: CellExecution) -> str:
        runtime_session = self._require_session(session.session_id)
        manager = getattr(runtime_session, "manager", None)
        if manager is None or not hasattr(manager, "interrupt_kernel"):
            raise RuntimeError("Attached external kernel interrupt is not supported yet")
        manager.interrupt_kernel()
        runtime_session.interrupted_client_ids.add(execution.client_id)
        runtime_session.pending_inputs.pop(execution.client_id, None)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def interrupt_handler(self, session: Session, execution: CellExecution) -> str:
        runtime_session = self._require_session(session.session_id)
        runtime_session.interrupted_client_ids.add(execution.client_id)
        runtime_session.pending_inputs.pop(execution.client_id, None)
        self.update_client_execution_status(session, execution.client_id, "interrupted")
        self.append_client_execution_event(
            session,
            execution.client_id,
            {"type": "execution_interrupted", "status": "interrupted"},
        )
        return "interrupted"

    def disconnect_session(self, session: Session, reason: str) -> None:
        self.release_session_clients(session.session_id, reason=reason)

    def sync_disconnect_deadline(self, session: Session, expires_at: float | None) -> float | None:
        connection_file = str(getattr(session, "connection", "") or "")
        if session.target.kind != "connection_file" or not connection_file:
            return expires_at
        if expires_at is not None:
            _set_attached_expiry(connection_file, expires_at)
            return expires_at
        shared = _get_attached_expiry(connection_file)
        if shared is not None:
            return shared
        _set_attached_expiry(connection_file, None)
        return None

    def expire_session(self, session: Session) -> None:
        self.stop_session(session)

    def shutdown_client(self, session: Session, client_id: str, reason: str) -> None:
        runtime_session = self._sessions.get(session.session_id)
        if runtime_session is not None:
            runtime_session.pending_inputs.pop(client_id, None)
            runtime_session.interrupted_client_ids.discard(client_id)
        super().shutdown_client(session, client_id, reason)

    def stop_session(self, session: Session) -> None:
        self.release_session_clients(session.session_id, reason="session_stop")
        runtime_session = self._sessions.pop(session.session_id, None)
        if runtime_session is None:
            return
        runtime_session.pending_inputs.clear()
        try:
            runtime_session.client.stop_channels()
        except Exception:
            pass
        manager = getattr(runtime_session, "manager", None)
        connection_file = str(getattr(runtime_session, "connection_file", "") or "")
        if manager is None:
            shutdown = getattr(runtime_session.client, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown(restart=False)
                except Exception:
                    pass
            if connection_file:
                _set_attached_expiry(connection_file, None)
                _signal_attached_supervisors(connection_file, sig=signal.SIGTERM, exclude_pid=os.getpid())
                _unregister_attached_supervisor(connection_file, os.getpid())
            return
        try:
            manager.shutdown_kernel(now=True)
        except Exception:
            pass
        try:
            manager.cleanup_resources()
        except Exception:
            pass

    def close(self) -> None:
        super().close()
        for session_id in list(self._sessions.keys()):
            runtime_session = self._sessions.pop(session_id, None)
            if runtime_session is None:
                continue
            runtime_session.pending_inputs.clear()
            try:
                runtime_session.client.stop_channels()
            except Exception:
                pass
            connection_file = str(getattr(runtime_session, "connection_file", "") or "")
            if connection_file:
                _unregister_attached_supervisor(connection_file, os.getpid())
            manager = getattr(runtime_session, "manager", None)
            if manager is None:
                continue
            try:
                manager.shutdown_kernel(now=True)
            except Exception:
                pass
            try:
                manager.cleanup_resources()
            except Exception:
                pass

    def _require_session(self, session_id: str) -> Any:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise RuntimeError(f"Unknown managed runtime session: {session_id}") from exc


class InMemorySessionStore:
    def __init__(self) -> None:
        from threading import RLock

        self._lock = RLock()
        self._sessions: dict[str, Session] = {}
        self._executions: dict[tuple[str, int], CellExecution] = {}

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.notebook_id] = session

    def get_by_notebook(self, notebook_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(notebook_id)

    def save_execution(self, notebook_id: str, execution: CellExecution) -> None:
        with self._lock:
            self._executions[(notebook_id, execution.cell_id)] = execution

    def get_execution(self, notebook_id: str, cell_id: int) -> CellExecution | None:
        with self._lock:
            return self._executions.get((notebook_id, cell_id))

    def list_executions(self, notebook_id: str) -> list[CellExecution]:
        with self._lock:
            return [
                execution
                for (stored_notebook_id, _cell_id), execution in self._executions.items()
                if stored_notebook_id == notebook_id
            ]

    def list_sessions(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())


def build_runtime() -> InMemoryKernelRuntime | ManagedKernelRuntime:
    mode = os.environ.get("JUSI_RUNTIME", "managed").strip().lower()
    if mode == "inmemory":
        return InMemoryKernelRuntime()
    return ManagedKernelRuntime()
