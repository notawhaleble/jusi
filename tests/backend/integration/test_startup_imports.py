"""Guard fresh-process startup costs by dependency boundary, not elapsed time."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "jusi.infrastructure.plugin_worker_child",
    "jusi.infrastructure.terminal_bridge",
    "jusi.plugins.vd.worker",
    "jusi.plugins.vd.application",
])
def test_fresh_runtime_roles_do_not_load_distribution_metadata(module):
    script = f"""
import sys
import {module}
assert 'importlib.metadata' not in sys.modules
assert not {{'IPython', 'ipykernel', 'pandas', 'visidata'}}.intersection(sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_cli_parsing_does_not_load_metadata_until_version_is_requested():
    script = """
import sys
from jusi.__main__ import build_parser
parser = build_parser()
assert parser.parse_args(['terminal-bridge', 'http://127.0.0.1:1', 'surf_test']).command == 'terminal-bridge'
assert 'importlib.metadata' not in sys.modules
from jusi import __version__
from importlib.metadata import version
assert __version__ == version('jusi')
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, "-m", "jusi", "--version"], capture_output=True, text=True, timeout=5)
    from importlib.metadata import version
    assert result.returncode == 0
    assert result.stdout == f"Jusi {version('jusi')}\n"
    assert result.stderr == ""
