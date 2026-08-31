from __future__ import annotations

import json
import os
from pathlib import Path

from jusi.application.ports import PluginCatalogDiscoveryResult
from jusi.application.plugin_workers import PluginWorkerManager
from jusi.application.supervisor import Supervisor
from jusi.domain.models import ProcessDiagnostics
from jusi.infrastructure.jupyter_kernel import ManagedJupyterKernelFactory
from jusi.infrastructure.plugin_worker import FreshProcessPluginWorkerFactory


class FixtureDiscovery:
    def __init__(self, catalog: dict) -> None:
        self.catalog = catalog

    def discover(self, *, discovery_id: str, timeout: float) -> PluginCatalogDiscoveryResult:
        assert timeout > 0
        catalog = {**self.catalog, "discovery_id": discovery_id}
        return PluginCatalogDiscoveryResult(catalog, ProcessDiagnostics(pid=os.getpid(), exit_code=0))


def test_real_kernel_handoff_starts_durable_exact_worker(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / "worker-request.json"
    (tmp_path / "fixture_exact_kernel.py").write_text(
        """
from IPython.display import display

HANDOFF_MIME = "application/vnd.jusi.handoff.v1+json"

def jusi_kernel_adapter_v1():
    return {
        "plugin_id": "fixture_exact",
        "plugin_version": "1.0.0",
        "families": [{"family_id": "fixture", "magic_name": "fixture"}],
    }

def load_ipython_extension(ipython):
    def fixture_magic(line, cell):
        display({HANDOFF_MIME: {
            "protocol_version": 1,
            "kind": "plugin.handoff",
            "plugin_id": "fixture_exact",
            "plugin_version": "1.0.0",
            "family_id": "fixture",
            "magic_name": "fixture",
            "payload": {"line": line, "body": cell},
        }}, raw=True)
    ipython.register_magic_function(fixture_magic, "cell", "fixture")
""",
        encoding="utf-8",
    )
    (tmp_path / "fixture_exact_worker.py").write_text(
        f"""
import json
from pathlib import Path

class Worker:
    def __init__(self, context):
        self.context = context

    def handle(self, operation, payload):
        Path({os.fspath(marker)!r}).write_text(json.dumps({{
            "operation": operation,
            "payload": payload,
            "client_id": self.context.client_id,
            "execution_id": self.context.execution_id,
        }}), encoding="utf-8")
        return {{"accepted": True}}

def create_worker(context):
    return Worker(context)
""",
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.fspath(tmp_path) + ((os.pathsep + existing) if existing else ""))
    catalog = {
        "protocol_version": 1,
        "catalog_version": 1,
        "discovery_id": "replaced_by_discovery",
        "plugins": [{
            "plugin_id": "fixture_exact",
            "plugin_version": "1.0.0",
            "distribution": "jusi-fixture-exact",
            "families": [{
                "family_id": "fixture",
                "magic_name": "fixture",
                "capabilities": ["execute", "followup"],
            }],
            "kernel_extensions": ["fixture_exact_kernel"],
            "worker_entry_point": "fixture_exact_worker:create_worker",
            "media_types": ["text/x-ansi"],
            "interaction": "request_response",
        }],
    }
    supervisor = Supervisor(
        ManagedJupyterKernelFactory(),
        FixtureDiscovery(catalog),
        PluginWorkerManager(FreshProcessPluginWorkerFactory(search_paths=[tmp_path])),
    )
    started = supervisor.start_kernel(
        notebook_id="nb_fixture", kernel_name="python3", trace_id="trace_start", timeout=8,
    )
    try:
        executed = supervisor.execute(
            kernel_id=started["kernel"]["kernel_id"],
            notebook_id="nb_fixture",
            cell_id="cell_fixture",
            code="%%fixture provider\nselect 1",
            trace_id="trace_execute",
            timeout=5,
        )
        client_id = executed["execution"]["client_id"]
        assert client_id is not None
        request = json.loads(marker.read_text(encoding="utf-8"))
        assert request == {
            "operation": "execute",
            "payload": {"line": "provider", "body": "select 1\n"},
            "client_id": client_id,
            "execution_id": executed["execution"]["execution_id"],
        }
        clients = supervisor.health()["clients"]
        assert len(clients) == 1
        assert clients[0]["client_id"] == client_id
        assert clients[0]["plugin_id"] == "fixture_exact"
        assert supervisor.health()["kernel"]["state"] == "on"
    finally:
        supervisor.stop_kernel(
            kernel_id=started["kernel"]["kernel_id"], trace_id="trace_stop", timeout=5,
        )
    assert supervisor.health()["clients"] == []
