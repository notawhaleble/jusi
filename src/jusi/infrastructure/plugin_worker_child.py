from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback
from typing import Any

from jusi.infrastructure.plugin_worker_channel import read_frame, write_frame
from jusi.plugin_api import WorkerContext
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

    while True:
        try:
            message = validate_plugin_worker_message(read_frame(control_input, limit=args.frame_limit))
        except (EOFError, ValueError, ProtocolValidationError):
            traceback.print_exc()
            return 21
        if message["plugin_worker_id"] != context.plugin_worker_id:
            print("plugin worker identity mismatch", file=sys.stderr)
            return 21
        if message["kind"] == "worker.shutdown":
            try:
                close = getattr(worker, "close", None)
                if callable(close):
                    close()
            except BaseException:
                traceback.print_exc()
                return 22
            write_frame(control_output, {
                "protocol_version": 1,
                "kind": "worker.stopped",
                "plugin_worker_id": context.plugin_worker_id,
                "request_id": message["request_id"],
                "trace_id": message["trace_id"],
            }, limit=args.frame_limit)
            return 0
        if message["kind"] != "worker.request":
            print("unexpected plugin worker control kind", file=sys.stderr)
            return 21
        try:
            result = worker.handle(message["operation"], message["payload"])
            if not isinstance(result, dict):
                raise TypeError("worker handle result must be an object")
            response = {
                "protocol_version": 1,
                "kind": "worker.result",
                "plugin_worker_id": context.plugin_worker_id,
                "request_id": message["request_id"],
                "trace_id": message["trace_id"],
                "operation": message["operation"],
                "result": result,
            }
            validate_plugin_worker_message(response)
            write_frame(control_output, response, limit=args.frame_limit)
        except BaseException as exc:
            traceback.print_exc()
            write_frame(control_output, _failure(message, exc), limit=args.frame_limit)
            return 23


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
