from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "sqlite_plugin"
PROBE_PREFIX = r'''
import importlib.abc
import json
import os
import sys

blocked = set(json.loads(os.environ["JUSI_BLOCKED_IMPORTS"]))

class BlockedImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in blocked:
            raise ImportError(f"forbidden fixture dependency: {root}")
        return None

sys.meta_path.insert(0, BlockedImports())
'''


def probe(source: str, *blocked: str) -> Any:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(FIXTURE_PATH), environment.get("PYTHONPATH", ""))
    )
    environment["JUSI_BLOCKED_IMPORTS"] = json.dumps(blocked)
    completed = subprocess.run(
        [sys.executable, "-c", PROBE_PREFIX + source],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_sqlite_fixture_process_roles_have_separate_import_boundaries() -> None:
    catalog = probe(
        r'''
import jusi_sqlite_catalog
assert not blocked.intersection(sys.modules)
print(json.dumps(jusi_sqlite_catalog.catalog_entry()))
''',
        "IPython", "visidata", "jusi",
    )
    worker_reference = catalog["worker_entry_point"]
    worker_module = worker_reference.partition(":")[0]
    kernel_modules = catalog["kernel_extensions"]

    launch = probe(
        f'''
import {worker_module} as worker_module_under_test
assert not {{"IPython", "visidata"}}.intersection(sys.modules)
worker = worker_module_under_test.create_worker(None)
try:
    result = worker.handle("execute", {{
        "alias": "main",
        "query": "select 1",
        "target": {{"provider": "sqlite", "path": "/private/not-opened.sqlite"}},
    }})
    request = result.core_requests[0]
    print(json.dumps({{"argv": list(request.argv)}}))
finally:
    worker.close()
''',
        "IPython", "visidata",
    )
    argv = launch["argv"]
    module_flag = argv.index("-m")
    application_module = argv[module_flag + 1]

    application_import = probe(
        f'''
import {application_module}
assert not blocked.intersection(sys.modules)
print(json.dumps({{"module": {application_module}.__name__}}))
''',
        "IPython", "visidata", "jusi",
    )

    assert application_import == {"module": application_module}
    assert len(kernel_modules) == 1
    assert len({"jusi_sqlite_catalog", kernel_modules[0], worker_module, application_module}) == 4
