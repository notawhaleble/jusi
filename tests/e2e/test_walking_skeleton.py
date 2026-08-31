from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


ROOT = Path(__file__).resolve().parents[2]


def require_loopback_bind() -> None:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
    except PermissionError:
        pytest.skip("sandbox forbids loopback sockets required by HTTP and Jupyter")
    finally:
        sock.close()


def request_json(base_url: str, path: str, *, method: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {path} failed with {exc.code}: {body}") from exc


def command(kind: str, trace_id: str, **payload) -> dict:
    return {
        "protocol_version": 1,
        "command_id": f"cmd_{kind}_{trace_id}",
        "trace_id": trace_id,
        "kind": kind,
        **payload,
    }


@pytest.mark.e2e
def test_service_kernel_execute_event_stop(tmp_path: Path) -> None:
    require_loopback_bind()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
    env["IPYTHONDIR"] = str(tmp_path / "ipython")
    env["JUPYTER_RUNTIME_DIR"] = str(tmp_path / "jupyter-runtime")
    process = subprocess.Popen(
        [sys.executable, "-m", "jusi", "serve", "--host", "127.0.0.1", "--port", "0"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    events: queue.Queue[dict] = queue.Queue()

    try:
        ready_line = process.stdout.readline()
        if not ready_line:
            raise AssertionError(f"service exited before readiness: {process.stderr.read()}")
        ready = json.loads(ready_line)
        base_url = f"http://{ready['host']}:{ready['port']}"

        def consume_events() -> None:
            with urlopen(base_url + "/v1/events?after=0", timeout=30) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                    if line.startswith("data: "):
                        events.put(json.loads(line[6:]))

        event_thread = threading.Thread(target=consume_events, daemon=True)
        event_thread.start()

        started = request_json(
            base_url,
            "/v1/kernels",
            method="POST",
            payload=command(
                "start_kernel",
                "trace_start_1",
                notebook_id="nb_walk",
                kernel_name="python3",
                idempotency_key="walk-start",
            ),
        )
        kernel_id = started["kernel"]["kernel_id"]
        executed = request_json(
            base_url,
            f"/v1/kernels/{kernel_id}/executions",
            method="POST",
            payload=command(
                "execute",
                "trace_execute_1",
                kernel_id=kernel_id,
                notebook_id="nb_walk",
                cell_id="cell_walk_1",
                code="1 + 1",
            ),
        )
        assert executed["execution"]["outcome"] == "succeeded"
        stopped = request_json(
            base_url,
            f"/v1/kernels/{kernel_id}",
            method="DELETE",
            payload=command(
                "stop_kernel",
                "trace_stop_1",
                kernel_id=kernel_id,
                idempotency_key="walk-stop",
            ),
        )
        assert stopped["cleanup"]["result"] == "stopped"
        repeated = request_json(
            base_url,
            f"/v1/kernels/{kernel_id}",
            method="DELETE",
            payload=command("stop_kernel", "trace_stop_2", kernel_id=kernel_id),
        )
        assert repeated["cleanup"]["result"] == "already_absent"

        expected = json.loads(
            (ROOT / "protocol" / "fixtures" / "v1" / "scenarios" / "walking-skeleton.json").read_text(
                encoding="utf-8"
            )
        )["expected_event_kinds"]
        observed: list[dict] = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(observed) < len(expected):
            try:
                observed.append(events.get(timeout=0.2))
            except queue.Empty:
                pass
        assert [event["kind"] for event in observed[: len(expected)]] == expected
        assert [event["sequence"] for event in observed] == list(range(1, len(observed) + 1))
        result_event = next(event for event in observed if event["kind"] == "execution.output")
        assert result_event["payload"]["media_type"] == "text/plain"
        assert result_event["payload"]["data"] == "2"
        assert observed[2]["payload"]["state"] == "on"
        assert observed[-2]["payload"]["state"] == "off"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
