from __future__ import annotations

import sys

from jusi.infrastructure.client_process import run_client_process
from jusi.interfaces.stdio import process_stream


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "client-process":
        return run_client_process()
    return process_stream(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
