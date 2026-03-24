import os
import unittest
from typing import Optional
from unittest.mock import patch

from jusi.domain.models import CellExecution, ExecutableCell, Session
from jusi.infrastructure.runtime import ManagedKernelRuntime, RuntimeDependencyError, build_runtime


class FakeClient:
    def __init__(self) -> None:
        self.executed: list[tuple[str, bool | None]] = []
        self.channels_stopped = False
        self.messages = [
            {
                "parent_header": {"msg_id": "msg-1"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            }
        ]

    def execute(self, code: str, store_history: Optional[bool] = None) -> str:
        self.executed.append((code, store_history))
        return "msg-1"

    def get_iopub_msg(self, timeout: int = 0) -> dict:
        _ = timeout
        return self.messages.pop(0)

    def stop_channels(self) -> None:
        self.channels_stopped = True


class FakeManager:
    def __init__(self) -> None:
        self.connection_file = "/tmp/kernel.json"
        self.interrupted = False
        self.shutdown = False
        self.cleaned = False

    def interrupt_kernel(self) -> None:
        self.interrupted = True

    def shutdown_kernel(self, now: bool = False) -> None:
        self.shutdown = now

    def cleanup_resources(self) -> None:
        self.cleaned = True


class ManagedRuntimeTest(unittest.TestCase):
    def test_build_runtime_uses_managed_mode(self) -> None:
        with patch.dict(os.environ, {"JUSI_RUNTIME": "managed"}, clear=False):
            runtime = build_runtime()
        self.assertIsInstance(runtime, ManagedKernelRuntime)

    def test_managed_runtime_start_execute_interrupt_and_stop(self) -> None:
        manager = FakeManager()
        client = FakeClient()
        with patch("jusi.infrastructure.runtime._start_new_kernel", return_value=(manager, client)):
            runtime = ManagedKernelRuntime()
            session_id, connection = runtime.start_managed("python3")

        self.assertEqual("/tmp/kernel.json", connection)
        session = Session(notebook_id="nb-1", session_id=session_id, connection=connection, kernel_name="python3")
        execution = CellExecution(cell_id=12, status="busy", owner_kind="kernel")

        status = runtime.execute_cell(
            session,
            ExecutableCell(cell_id=12, kind="code", syntax="python", main_lines=["print(1)"]),
            execution,
        )
        self.assertEqual("done", status)
        self.assertEqual([("print(1)", None)], client.executed)

        interrupt_status = runtime.interrupt_kernel(session, execution)
        self.assertEqual("interrupted", interrupt_status)
        self.assertTrue(manager.interrupted)

        runtime.stop_session(session)
        self.assertTrue(client.channels_stopped)
        self.assertTrue(manager.shutdown)
        self.assertTrue(manager.cleaned)

    def test_managed_runtime_reports_missing_dependency_cleanly(self) -> None:
        with patch("builtins.__import__", side_effect=ModuleNotFoundError("missing")):
            runtime = ManagedKernelRuntime()
            with self.assertRaises(RuntimeDependencyError):
                runtime.start_managed("python3")

    def test_managed_runtime_reports_missing_kernelspec_cleanly(self) -> None:
        class FakeNoSuchKernel(Exception):
            pass

        with patch("jusi.infrastructure.runtime._start_new_kernel", side_effect=RuntimeDependencyError("missing kernelspec")):
            runtime = ManagedKernelRuntime()
            with self.assertRaises(RuntimeDependencyError):
                runtime.start_managed("python3")


if __name__ == "__main__":
    unittest.main()
