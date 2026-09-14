"""Opt-in network/install gate, discovered by the ordinary pytest suite."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.e2e
def test_installed_distribution():
    wheel = os.environ.get("JUSI_DISTRIBUTION_WHEEL")
    if not wheel:
        pytest.skip("Set JUSI_DISTRIBUTION_WHEEL to test a built wheel in a fresh environment")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/check-distribution.py"), wheel],
                            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=900)
    assert result.returncode == 0, result.stdout
