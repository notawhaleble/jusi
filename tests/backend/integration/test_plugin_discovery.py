from __future__ import annotations

import os
from pathlib import Path
import sys
import textwrap

import pytest

from jusi.infrastructure.plugin_discovery import FreshProcessPluginCatalogDiscovery, PluginDiscoveryError


def entry_source(
    *,
    plugin_id: str,
    distribution: str,
    version: str,
    prelude: str = "",
    family_id: str = "sql",
    magic_name: str = "sql",
) -> str:
    provider = textwrap.dedent(
        f"""
        def catalog_entry():
            return {{
                "plugin_id": {plugin_id!r},
                "plugin_version": {version!r},
                "distribution": {distribution!r},
                "families": [{{
                    "family_id": {family_id!r},
                    "magic_name": {magic_name!r},
                    "capabilities": ["execute", "complete"],
                    "presentation": {{"syntax": "sql", "indent": "sql"}},
                }}],
                "kernel_extensions": ["fixture.kernel"],
                "worker_entry_point": "fixture.worker:main",
                "media_types": ["text/plain", "text/x-ansi"],
                "interaction": "request_response",
            }}
        """
    )
    return (prelude.rstrip() + "\n" if prelude else "") + provider


def install_fixture_distribution(
    root: Path,
    *,
    plugin_id: str,
    distribution: str | None = None,
    version: str = "1.0.0",
    source: str | None = None,
) -> str:
    distribution_name = distribution or f"jusi-{plugin_id}"
    module_name = f"fixture_{plugin_id}"
    module_path = root / f"{module_name}.py"
    module_source = source or entry_source(plugin_id=plugin_id, distribution=distribution_name, version=version)
    module_path.write_text(module_source, encoding="utf-8")

    dist_info = root / f"{distribution_name.replace('-', '_')}.dist-info"
    dist_info.mkdir(exist_ok=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {distribution_name}\nVersion: {version}\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        f"[jusi.plugins.v1]\n{plugin_id} = {module_name}:catalog_entry\n",
        encoding="utf-8",
    )
    return module_name


def discoverer(
    root: Path,
    *,
    stderr_limit: int = 16 * 1024,
    result_limit: int = 1024 * 1024,
) -> FreshProcessPluginCatalogDiscovery:
    return FreshProcessPluginCatalogDiscovery(
        search_paths=[root],
        stderr_limit=stderr_limit,
        result_limit=result_limit,
    )


def test_empty_plugin_environment_returns_a_valid_catalog(tmp_path: Path) -> None:
    result = discoverer(tmp_path).discover(discovery_id="discovery_empty", timeout=2)
    assert result.catalog["plugins"] == []
    assert result.process.exit_code == 0


def test_production_environment_enumeration_without_explicit_search_path() -> None:
    result = FreshProcessPluginCatalogDiscovery().discover(discovery_id="discovery_environment", timeout=2)
    assert result.catalog["discovery_id"] == "discovery_environment"


def test_discovery_is_fresh_deterministic_and_never_imports_provider_in_parent(tmp_path: Path) -> None:
    module_name = install_fixture_distribution(tmp_path, plugin_id="sqlite")
    adapter = discoverer(tmp_path)

    first = adapter.discover(discovery_id="discovery_first", timeout=2)
    assert [item["plugin_id"] for item in first.catalog["plugins"]] == ["sqlite"]
    assert module_name not in sys.modules

    source = entry_source(plugin_id="sqlite", distribution="jusi-sqlite", version="20.0.0", prelude='print("plugin noise")')
    install_fixture_distribution(tmp_path, plugin_id="sqlite", version="20.0.0", source=source)
    second = adapter.discover(discovery_id="discovery_second", timeout=2)

    assert second.catalog["plugins"][0]["plugin_version"] == "20.0.0"
    assert second.process.pid != first.process.pid
    assert module_name not in sys.modules


def test_broken_plugin_prevents_partial_catalog_and_is_repaired_by_next_discovery(tmp_path: Path) -> None:
    install_fixture_distribution(tmp_path, plugin_id="healthy")
    broken_source = "raise RuntimeError('broken discovery import')\n"
    install_fixture_distribution(tmp_path, plugin_id="broken", source=broken_source)
    adapter = discoverer(tmp_path)

    with pytest.raises(PluginDiscoveryError) as raised:
        adapter.discover(discovery_id="discovery_broken", timeout=2)
    assert raised.value.reason == "plugin_error"
    assert raised.value.entry_point == "broken"
    assert raised.value.distribution == "jusi-broken"

    install_fixture_distribution(tmp_path, plugin_id="broken")
    repaired = adapter.discover(discovery_id="discovery_repaired", timeout=2)
    assert [item["plugin_id"] for item in repaired.catalog["plugins"]] == ["broken", "healthy"]


def test_malformed_entry_and_family_conflict_are_typed(tmp_path: Path) -> None:
    malformed = "def catalog_entry():\n    return {'plugin_id': 'malformed'}\n"
    install_fixture_distribution(tmp_path, plugin_id="malformed", source=malformed)
    with pytest.raises(PluginDiscoveryError) as malformed_error:
        discoverer(tmp_path).discover(discovery_id="discovery_malformed", timeout=2)
    assert malformed_error.value.reason == "plugin_error"
    assert malformed_error.value.entry_point == "malformed"

    other = tmp_path / "conflict"
    other.mkdir()
    install_fixture_distribution(other, plugin_id="one", source=entry_source(
        plugin_id="one", distribution="jusi-one", version="1.0.0", family_id="sql", magic_name="query"
    ))
    install_fixture_distribution(other, plugin_id="two", source=entry_source(
        plugin_id="two", distribution="jusi-two", version="1.0.0", family_id="warehouse", magic_name="query"
    ))
    with pytest.raises(PluginDiscoveryError) as conflict_error:
        discoverer(other).discover(discovery_id="discovery_conflict", timeout=2)
    assert conflict_error.value.reason == "conflict"


def test_timeout_terminates_the_owned_discovery_process(tmp_path: Path) -> None:
    source = entry_source(
        plugin_id="slow",
        distribution="jusi-slow",
        version="1.0.0",
        prelude="import time\ntime.sleep(10)",
    )
    install_fixture_distribution(tmp_path, plugin_id="slow", source=source)

    with pytest.raises(PluginDiscoveryError) as raised:
        discoverer(tmp_path).discover(discovery_id="discovery_timeout", timeout=0.1)
    assert raised.value.reason == "timeout"
    assert raised.value.diagnostics is not None
    assert raised.value.diagnostics.pid is not None


def test_process_exit_preserves_bounded_stderr(tmp_path: Path) -> None:
    source = "import os\nos.write(2, b'x' * 20000)\nos._exit(9)\n"
    install_fixture_distribution(tmp_path, plugin_id="crash", source=source)

    with pytest.raises(PluginDiscoveryError) as raised:
        discoverer(tmp_path, stderr_limit=128).discover(discovery_id="discovery_crash", timeout=2)
    assert raised.value.reason == "process_exited"
    diagnostics = raised.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.exit_code == 9
    assert diagnostics.stderr_truncated is True
    assert len(diagnostics.stderr_excerpt.encode("utf-8")) <= 128


def test_spawn_and_result_limit_failures_are_typed(tmp_path: Path) -> None:
    missing_python = tmp_path / "missing-python"
    with pytest.raises(PluginDiscoveryError) as spawn_error:
        FreshProcessPluginCatalogDiscovery(python_executable=os.fspath(missing_python)).discover(
            discovery_id="discovery_spawn",
            timeout=2,
        )
    assert spawn_error.value.reason == "spawn_failed"

    with pytest.raises(PluginDiscoveryError) as size_error:
        discoverer(tmp_path, result_limit=8).discover(discovery_id="discovery_too_large", timeout=2)
    assert size_error.value.reason == "protocol_violation"


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal identity is platform-specific")
def test_process_signal_is_distinct_from_exit(tmp_path: Path) -> None:
    source = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
    install_fixture_distribution(tmp_path, plugin_id="signal", source=source)

    with pytest.raises(PluginDiscoveryError) as raised:
        discoverer(tmp_path).discover(discovery_id="discovery_signal", timeout=2)
    assert raised.value.reason == "process_signalled"
    assert raised.value.diagnostics is not None
    assert raised.value.diagnostics.signal == 9
