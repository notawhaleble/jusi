from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback
import threading
import queue
import signal
import time
from jusi.infrastructure.plugin_worker_channel import WorkerFrameError
from typing import Any

from jusi.infrastructure.plugin_worker_channel import read_frame, write_frame, write_editor_result
from jusi.plugin_api import WorkerContext, WorkerResult, OperationRejected
from jusi.protocol import ProtocolValidationError, validate_plugin_worker_message


def _resolve(reference: str) -> Any:
    module_name, separator, attribute_path = reference.partition(":")
    if not separator or not module_name or not attribute_path:
        raise ValueError("worker_entry_point must use module:attribute syntax")
    value: Any = importlib.import_module(module_name)
    for attribute in attribute_path.split("."):
        if not attribute:
            raise ValueError("worker_entry_point contains an empty attribute")
        value = getattr(value, attribute)
    return value


def _failure(request: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "kind": "worker.failure",
        "plugin_worker_id": request["plugin_worker_id"],
        "request_id": request["request_id"],
        "trace_id": request["trace_id"],
        "operation": request["operation"],
        "failure": {
            "reason": "plugin_error",
            "message": f"{type(exc).__name__}: {exc}"[:1000],
            "retryable": False,
        },
    }


def run(args: argparse.Namespace) -> int:
    control_input = os.fdopen(os.dup(sys.stdin.fileno()), "rb", buffering=0)
    control_output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    null_fd = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(null_fd, sys.stdin.fileno())
    finally:
        os.close(null_fd)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

    for search_path in reversed(args.search_path):
        sys.path.insert(0, search_path)

    context = WorkerContext(
        plugin_worker_id=args.plugin_worker_id,
        runtime_id=args.runtime_id,
        plugin_id=args.plugin_id,
        family_id=args.family_id,
        client_id=args.client_id,
        execution_id=args.execution_id,
    )
    try:
        factory = _resolve(args.entry_point)
        if not callable(factory):
            raise TypeError("worker entry point must resolve to a factory")
        worker = factory(context)
        if not callable(getattr(worker, "handle", None)):
            raise TypeError("worker factory must return an object with handle(operation, payload)")
    except BaseException:
        traceback.print_exc()
        return 20

    write_frame(control_output, {
        "protocol_version": 1,
        "kind": "worker.ready",
        "plugin_worker_id": context.plugin_worker_id,
        "runtime_id": context.runtime_id,
        "plugin_id": context.plugin_id,
        "family_id": context.family_id,
        "client_id": context.client_id,
        "execution_id": context.execution_id,
        "pid": os.getpid(),
    }, limit=args.frame_limit)

    write_lock = threading.Lock()
    active_lock = threading.Lock()
    active: dict[str, Any] = {}

    def send(response: dict[str, Any]) -> None:
        validate_plugin_worker_message(response)
        with write_lock:
            writer = write_editor_result if response["kind"] == "worker.result" and response["operation"] == "editor_action" else write_frame
            writer(control_output, response, limit=args.frame_limit)

    def response_for(message: dict[str, Any], result: dict[str, Any], core_requests=None):
        return {
            **{key: message[key] for key in ("protocol_version", "plugin_worker_id", "request_id", "trace_id", "operation")},
            "kind": "worker.result", "result": result, "core_requests": core_requests or [],
        }

    def reject(message: dict[str, Any], exc: OperationRejected) -> None:
        response = _failure(message, exc)
        response["kind"] = "worker.rejected"
        response["failure"]["reason"] = exc.reason
        send(response)

    def handle(message: dict[str, Any]) -> None:
        try:
            handled = worker.handle(message["operation"], message["payload"])
            if isinstance(handled, WorkerResult):
                result = handled.result
                core_requests = [request.to_dict() for request in handled.core_requests]
            elif isinstance(handled, dict):
                result, core_requests = handled, []
            else:
                raise TypeError("worker handle result must be an object or WorkerResult")
            with active_lock:
                send(response_for(message, result, core_requests))
                active.clear()
        except WorkerFrameError as exc:
            if message["operation"] != "editor_action":
                traceback.print_exc()
                send(_failure(message, exc))
                return
            with active_lock:
                reject(message, OperationRejected("Export could not be encoded", reason="invalid_request"))
                active.clear()
        except OperationRejected as exc:
            with active_lock:
                reject(message, exc)
                active.clear()
        except BaseException as exc:
            traceback.print_exc()
            send(_failure(message, exc))
            # Keep the process available for supervisor-owned fatal cleanup.
            sys.stderr.flush()

    requests: queue.Queue[dict[str, Any] | int] = queue.Queue()

    def receive() -> None:
        while True:
            try:
                message = validate_plugin_worker_message(read_frame(control_input, limit=args.frame_limit))
            except (EOFError, ValueError, ProtocolValidationError):
                # The private owner channel cannot reconnect. Give thread-affine
                # cleanup a chance, then terminate this owned worker group even
                # if a handler or non-daemon plugin thread never returns.
                def owner_lost():
                    time.sleep(1.0)
                    if os.name == "posix" and os.getpgrp() == os.getpid():
                        os.killpg(os.getpgrp(), signal.SIGKILL)
                    os._exit(21)
                threading.Thread(target=owner_lost, daemon=True).start()
                requests.put(21)
                try:
                    traceback.print_exc()
                except OSError:
                    pass
                return
            if message["plugin_worker_id"] != context.plugin_worker_id:
                requests.put(21)
                return
            if message["kind"] == "worker.shutdown":
                requests.put(message)
                return
            if message["kind"] != "worker.request":
                requests.put(21)
                return
            if message["operation"] == "interrupt":
                with active_lock:
                    if active.get("request_id") != message["payload"].get("target_request_id") or not active:
                        reject(message, OperationRejected("Work identity is no longer active", reason="conflict"))
                        continue
                    hook = getattr(worker, "interrupt", None)
                    if not callable(hook):
                        reject(message, OperationRejected("Worker has no interrupt hook", reason="unsupported"))
                        continue
                    if active.get("interrupted"):
                        send(response_for(message, {"result": "already_requested"}))
                        continue
                    try:
                        # Runs concurrently with handle(); must request cancellation
                        # promptly, never wait for handle() to finish.
                        hook()
                        active["interrupted"] = True
                        send(response_for(message, {"result": "requested"}))
                    except Exception as exc:
                        reject(message, OperationRejected(str(exc) or type(exc).__name__))
                continue
            with active_lock:
                if active:
                    reject(message, OperationRejected("Worker is busy", reason="conflict"))
                    continue
                active["request_id"] = message["request_id"]
            requests.put(message)

    threading.Thread(target=receive, daemon=True).start()
    # Factory, all ordinary handlers, and close share one thread. Database
    # connections and other thread-affine sessions retain their owning thread.
    while True:
        message = requests.get()
        if isinstance(message, int):
            close = getattr(worker, "close", None)
            if callable(close):
                try:
                    close()
                except BaseException:
                    traceback.print_exc()
            return message
        if message["kind"] == "worker.shutdown":
            try:
                close = getattr(worker, "close", None)
                if callable(close):
                    close()
            except BaseException:
                traceback.print_exc()
                return 22
            send({"protocol_version": 1, "kind": "worker.stopped",
                  "plugin_worker_id": context.plugin_worker_id,
                  "request_id": message["request_id"], "trace_id": message["trace_id"]})
            return 0
        handle(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--entry-point", required=True)
    parser.add_argument("--plugin-worker-id", required=True)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--family-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--frame-limit", type=int, required=True)
    parser.add_argument("--search-path", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
