from __future__ import annotations

import argparse
import asyncio

from jusi.infrastructure.terminal_bridge import terminal_bridge_main
from jusi.service import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jusi")
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve", help="start the Jusi HTTP/SSE service")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--config")
    bridge_parser = subparsers.add_parser(
        "terminal-bridge", help="attach the local terminal to one Jusi terminal surface"
    )
    bridge_parser.add_argument("base_url")
    bridge_parser.add_argument("surface_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "terminal-bridge":
        return terminal_bridge_main(args.base_url, args.surface_id)
    if args.command != "serve":
        parser.print_help()
        return 2
    asyncio.run(serve(args.host, args.port, config_path=args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
