from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.9/3.10
    import tomli as tomllib  # type: ignore[no-redef]

from jusi.application.ports import RuntimeConfigurationError


DEFAULT_CONFIG_PATH = Path("~/.jusi/jusi.toml")
DEFAULT_CONFIG_BYTES = 1024 * 1024
_LOCATION = re.compile(r"\(at line (?P<line>\d+), column (?P<column>\d+)\)")


class TomlRuntimeConfigurationLoader:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        required: bool = False,
        max_bytes: int = DEFAULT_CONFIG_BYTES,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._path = (DEFAULT_CONFIG_PATH if path is None else Path(path)).expanduser()
        self._required = required
        self._max_bytes = max_bytes

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        try:
            with self._path.open("rb") as stream:
                data = stream.read(self._max_bytes + 1)
        except FileNotFoundError as exc:
            if not self._required:
                return {}
            raise RuntimeConfigurationError(
                "Explicit runtime configuration file does not exist",
                path=str(self._path),
            ) from exc
        except OSError as exc:
            raise RuntimeConfigurationError(
                f"Runtime configuration could not be read: {exc.strerror or exc.__class__.__name__}",
                path=str(self._path),
            ) from exc

        if len(data) > self._max_bytes:
            raise RuntimeConfigurationError(
                "Runtime configuration exceeds the size limit",
                path=str(self._path),
            )
        try:
            value = tomllib.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            match = _LOCATION.search(str(exc))
            raise RuntimeConfigurationError(
                "Runtime configuration is not valid TOML",
                path=str(self._path),
                line=int(match.group("line")) if match else None,
                column=int(match.group("column")) if match else None,
            ) from exc

        try:
            _require_data_only(value)
        except ValueError as exc:
            raise RuntimeConfigurationError(str(exc), path=str(self._path)) from exc
        return value


def _require_data_only(value: object, location: str = "configuration") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{location} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_data_only(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{location} contains a non-string key")
            _require_data_only(item, f"{location}.{key}")
        return
    raise ValueError(f"{location} contains unsupported TOML value type {type(value).__name__}")
