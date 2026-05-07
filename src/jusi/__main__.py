from __future__ import annotations

import sys

from jusi.infrastructure.client_process import run_terminal_attach
from jusi.infrastructure.client_runtime_entrypoint import run_client_runtime
from jusi.infrastructure.plugin_runtime import run_plugin_runtime
from jusi.interfaces.stdio import process_stream


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "plugin-runtime":
        return run_plugin_runtime()
    if args and args[0] == "client-runtime":
        return run_client_runtime()
    if args and args[0] == "client-process":
        if len(args) > 1 and args[1] == "terminal-attach":
            return run_terminal_attach()
        return run_client_runtime("transcript")
    return process_stream(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
