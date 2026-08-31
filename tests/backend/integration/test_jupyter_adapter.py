from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernel


class FakeManager:
    def __init__(self) -> None:
        self.provisioner = SimpleNamespace(pid=9876, process=SimpleNamespace(poll=lambda: None))
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def is_alive(self) -> bool:
        return True

    def shutdown_kernel(self, *, now: bool, restart: bool) -> None:
        self.shutdown_calls.append((now, restart))


class FakeClient:
    def __init__(self) -> None:
        self.stopped = False

    def execute_interactive(self, code: str, **kwargs):  # type: ignore[no-untyped-def]
        assert code == "1 + 1"
        hook = kwargs["output_hook"]
        hook({"msg_type": "stream", "content": {"name": "stdout", "text": "\u001b[32mhello\u001b[0m\n"}})
        hook({"msg_type": "execute_result", "content": {"data": {"text/plain": "2"}}})
        return {"content": {"status": "ok"}}

    def stop_channels(self) -> None:
        self.stopped = True


def test_jupyter_adapter_preserves_ansi_and_result_media() -> None:
    stderr_file = tempfile.NamedTemporaryFile(delete=False)
    stderr_path = stderr_file.name
    manager = FakeManager()
    client = FakeClient()
    kernel = ManagedJupyterKernel(manager, client, stderr_file, stderr_path)  # type: ignore[arg-type]

    result = kernel.execute("1 + 1", timeout=1)

    assert result.outcome == "succeeded"
    assert result.outputs[0].media_type == "text/x-ansi"
    assert result.outputs[0].data == "\u001b[32mhello\u001b[0m\n"
    assert result.outputs[1].media_type == "text/plain"
    assert result.outputs[1].data == "2"

    kernel.stop(timeout=1)
    assert manager.shutdown_calls == [(False, False)]
    assert client.stopped
    assert not Path(stderr_path).exists()
