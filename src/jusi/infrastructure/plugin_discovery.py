from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Any, Sequence

from jusi.application.ports import PluginCatalogDiscoveryError, PluginCatalogDiscoveryResult
from jusi.domain.models import ProcessDiagnostics
from jusi.plugin_api import validate_catalog_claims
from jusi.protocol import ProtocolValidationError


DEFAULT_STDERR_LIMIT = 16 * 1024
DEFAULT_RESULT_LIMIT = 1024 * 1024


PluginDiscoveryError = PluginCatalogDiscoveryError


class _BoundedStderrReader(threading.Thread):
    def __init__(self, stream: Any, limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._tail = bytearray()
        self.total = 0

    def run(self) -> None:
        while True:
            chunk = self._stream.read(8192)
            if not chunk:
                return
            self.total += len(chunk)
            self._tail.extend(chunk)
            if len(self._tail) > self._limit:
                del self._tail[: len(self._tail) - self._limit]

    @property
    def excerpt(self) -> str:
        return bytes(self._tail).decode("utf-8", errors="replace")

    @property
    def truncated(self) -> bool:
        return self.total > self._limit


class FreshProcessPluginCatalogDiscovery:
    def __init__(
        self,
        *,
        python_executable: str | None = None,
        search_paths: Sequence[str | os.PathLike[str]] | None = None,
        stderr_limit: int = DEFAULT_STDERR_LIMIT,
        result_limit: int = DEFAULT_RESULT_LIMIT,
    ) -> None:
        self._python_executable = python_executable or sys.executable
        self._search_paths = None if search_paths is None else tuple(os.fspath(path) for path in search_paths)
        self._stderr_limit = stderr_limit
        self._result_limit = result_limit

    def discover(self, *, discovery_id: str, timeout: float) -> PluginDiscoveryResult:
        if not discovery_id or timeout <= 0:
            raise ValueError("discovery_id must be non-empty and timeout must be positive")
        with tempfile.TemporaryDirectory(prefix="jusi-plugin-discovery-") as directory:
            result_path = Path(directory) / "result.json"
            command = [
                self._python_executable,
                "-m",
                "jusi.infrastructure.plugin_discovery_child",
                "--discovery-id",
                discovery_id,
                "--result",
                os.fspath(result_path),
            ]
            if self._search_paths is not None:
                for search_path in self._search_paths:
                    command.extend(("--search-path", search_path))
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=os.name != "nt",
                )
            except OSError as exc:
                raise PluginDiscoveryError(
                    f"Could not start plugin discovery: {exc}",
                    reason="spawn_failed",
                    retryable=True,
                ) from exc

            assert process.stderr is not None
            stderr_reader = _BoundedStderrReader(process.stderr, self._stderr_limit)
            stderr_reader.start()
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate(process)
            finally:
                self._kill_remaining_group(process)
                stderr_reader.join(timeout=2)
                process.stderr.close()

            diagnostics = self._diagnostics(process, stderr_reader)
            if timed_out:
                raise PluginDiscoveryError(
                    "Plugin discovery timed out",
                    reason="timeout",
                    retryable=True,
                    diagnostics=diagnostics,
                )
            if process.returncode is None:
                self._terminate(process)
                diagnostics = self._diagnostics(process, stderr_reader)
            if process.returncode is None or process.returncode != 0:
                signalled = process.returncode is not None and process.returncode < 0
                raise PluginDiscoveryError(
                    "Plugin discovery process was signalled" if signalled else "Plugin discovery process exited",
                    reason="process_signalled" if signalled else "process_exited",
                    retryable=True,
                    diagnostics=diagnostics,
                )

            payload = self._read_result(result_path, diagnostics)
            if payload.get("child_pid") != process.pid:
                raise self._protocol_error("Plugin discovery child identity mismatch", diagnostics)
            if payload.get("ok") is False:
                failure = payload.get("failure")
                if not isinstance(failure, dict):
                    raise self._protocol_error("Plugin discovery failure result is malformed", diagnostics)
                reason = failure.get("reason")
                if reason not in {"plugin_error", "conflict"}:
                    raise self._protocol_error("Plugin discovery failure reason is invalid", diagnostics)
                raise PluginDiscoveryError(
                    str(failure.get("message") or "Plugin discovery failed"),
                    reason=reason,
                    retryable=reason == "plugin_error",
                    diagnostics=diagnostics,
                    entry_point=str(failure.get("entry_point") or ""),
                    distribution=str(failure.get("distribution") or ""),
                )
            if payload.get("ok") is not True:
                raise self._protocol_error("Plugin discovery result has no outcome", diagnostics)
            try:
                catalog = validate_catalog_claims(payload.get("catalog"))
            except ProtocolValidationError as exc:
                raise self._protocol_error(f"Plugin discovery returned an invalid catalog: {exc}", diagnostics) from exc
            if catalog["discovery_id"] != discovery_id:
                raise self._protocol_error("Plugin discovery result used the wrong discovery identity", diagnostics)
            return PluginCatalogDiscoveryResult(catalog=catalog, process=diagnostics)

    def _read_result(self, path: Path, diagnostics: ProcessDiagnostics) -> dict[str, Any]:
        try:
            size = path.stat().st_size
        except FileNotFoundError as exc:
            raise self._protocol_error("Plugin discovery produced no result", diagnostics) from exc
        if size > self._result_limit:
            raise self._protocol_error("Plugin discovery result exceeded its size limit", diagnostics)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise self._protocol_error("Plugin discovery result was not valid bounded JSON", diagnostics) from exc
        if not isinstance(value, dict) or set(value) not in (
            {"ok", "child_pid", "catalog"},
            {"ok", "child_pid", "failure"},
        ):
            raise self._protocol_error("Plugin discovery result envelope is malformed", diagnostics)
        return value

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass

    @staticmethod
    def _kill_remaining_group(process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt":
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _diagnostics(process: subprocess.Popen[bytes], reader: _BoundedStderrReader) -> ProcessDiagnostics:
        returncode = process.returncode
        return ProcessDiagnostics(
            pid=process.pid,
            exit_code=returncode if returncode is not None and returncode >= 0 else None,
            signal=-returncode if returncode is not None and returncode < 0 else None,
            stderr_excerpt=reader.excerpt,
            stderr_truncated=reader.truncated,
        )

    @staticmethod
    def _protocol_error(message: str, diagnostics: ProcessDiagnostics) -> PluginDiscoveryError:
        return PluginDiscoveryError(
            message,
            reason="protocol_violation",
            retryable=False,
            diagnostics=diagnostics,
        )
