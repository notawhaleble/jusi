from __future__ import annotations

import argparse
import asyncio

from jusi.service import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jusi")
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve", help="start the Jusi HTTP/SSE service")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "serve":
        parser.print_help()
        return 2
    asyncio.run(serve(args.host, args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
