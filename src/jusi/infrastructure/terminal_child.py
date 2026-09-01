from __future__ import annotations

import fcntl
import os
import sys
import termios


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] != "--" or len(arguments) < 2:
        print("terminal child requires -- followed by an argv vector", file=sys.stderr)
        return 126
    command = arguments[1:]
    try:
        # Popen made this launcher a session leader. Claim the already-sized
        # slave only now, in the isolated child, then replace the launcher.
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.execvpe(command[0], command, os.environ)
    except BaseException as exc:
        print(f"terminal child exec failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
