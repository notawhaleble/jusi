from __future__ import annotations

import sys

from jusi.interfaces.stdio import process_stream


def main() -> int:
    return process_stream(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
