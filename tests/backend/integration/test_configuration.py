from __future__ import annotations

from pathlib import Path

import pytest

from jusi.application.ports import RuntimeConfigurationError
from jusi.infrastructure.configuration import TomlRuntimeConfigurationLoader


def test_loads_target_configuration_as_data_only_mapping(tmp_path: Path) -> None:
    path = tmp_path / "jusi.toml"
    path.write_text(
        "[sql.main]\nprovider = \"sqlite\"\npath = \"/tmp/main.sqlite\"\n",
        encoding="utf-8",
    )

    assert TomlRuntimeConfigurationLoader(path, required=True).load() == {
        "sql": {"main": {"provider": "sqlite", "path": "/tmp/main.sqlite"}},
    }


def test_missing_default_is_empty_but_missing_explicit_path_fails(tmp_path: Path) -> None:
    path = tmp_path / "missing.toml"
    assert TomlRuntimeConfigurationLoader(path).load() == {}

    with pytest.raises(RuntimeConfigurationError) as captured:
        TomlRuntimeConfigurationLoader(path, required=True).load()

    assert captured.value.path == str(path)
    assert str(captured.value) == "Explicit runtime configuration file does not exist"


def test_malformed_toml_reports_location_without_file_content(tmp_path: Path) -> None:
    secret = "do-not-leak-this-value"
    path = tmp_path / "broken.toml"
    path.write_text(f"[sql.main]\npassword = {secret}\n", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError) as captured:
        TomlRuntimeConfigurationLoader(path, required=True).load()

    assert captured.value.path == str(path)
    assert captured.value.line == 2
    assert captured.value.column is not None
    assert secret not in str(captured.value)


def test_toml_native_dates_are_rejected_before_private_injection(tmp_path: Path) -> None:
    path = tmp_path / "dated.toml"
    path.write_text("cutover = 2026-09-02\n", encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError) as captured:
        TomlRuntimeConfigurationLoader(path, required=True).load()

    assert "unsupported TOML value type date" in str(captured.value)


def test_configuration_read_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "large.toml"
    path.write_text('value = "too large"\n', encoding="utf-8")

    with pytest.raises(RuntimeConfigurationError) as captured:
        TomlRuntimeConfigurationLoader(path, required=True, max_bytes=8).load()

    assert str(captured.value) == "Runtime configuration exceeds the size limit"
