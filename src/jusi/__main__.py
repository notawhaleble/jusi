from __future__ import annotations

import argparse
import asyncio
from importlib.metadata import version

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jusi")
    parser.add_argument("--version", action="version", version=f"Jusi {version('jusi')}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("frontend-path", help="print the installed Neovim runtime directory")
    serve_parser = subparsers.add_parser("serve", help="start the Jusi HTTP/SSE service")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--config")
    serve_parser.add_argument("--owner-stdin", action="store_true", help="stop when the spawning editor closes stdin")
    bridge_parser = subparsers.add_parser(
        "terminal-bridge", help="attach the local terminal to one Jusi terminal surface"
    )
    bridge_parser.add_argument("base_url")
    bridge_parser.add_argument("surface_id")
    bridge_parser.add_argument("--editor-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "frontend-path":
        from jusi.frontend import frontend_path
        try:
            print(frontend_path())
        except RuntimeError as exc:
            parser.exit(1, f"jusi: {exc}\n")
        return 0
    if args.command == "terminal-bridge":
        from jusi.infrastructure.terminal_bridge import terminal_bridge_main
        return terminal_bridge_main(args.base_url, args.surface_id, args.editor_id)
    if args.command != "serve":
        parser.print_help()
        return 2
    from jusi.service import serve
    asyncio.run(serve(args.host, args.port, config_path=args.config, owner_stdin=args.owner_stdin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
