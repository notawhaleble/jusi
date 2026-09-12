import json
import os
from pathlib import Path
import tempfile

import pytest

from jusi.kernel_artifacts import publish_json, consume_json_file


def test_large_artifact_consumes_only_its_private_source(monkeypatch, tmp_path):
    with tempfile.TemporaryDirectory(prefix="jusi-artifacts-") as root:
        monkeypatch.setenv("JUSI_KERNEL_ARTIFACT_DIRECTORY", root)
        value = {"text": "α" * 1000000}
        reference = publish_json(value)
        source = Path(reference["path"])
        assert source.stat().st_mode & 0o777 == 0o600
        output = tmp_path / "snapshot.json"
        consume_json_file(reference, output)
        assert not source.exists()
        assert json.loads(output.read_text()) == value
        assert output.stat().st_mode & 0o777 == 0o600
        # A symlink cannot substitute an unrelated private file.
        source.symlink_to(output)
        with pytest.raises(OSError):
            consume_json_file(reference, tmp_path / "bad.json")
        assert output.exists()


def test_failed_snapshot_encoding_removes_partial_file(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="jusi-artifacts-") as root:
        monkeypatch.setenv("JUSI_KERNEL_ARTIFACT_DIRECTORY", root)
        with pytest.raises(TypeError):
            publish_json({"unsupported": object()})
        assert list(Path(root).iterdir()) == []
