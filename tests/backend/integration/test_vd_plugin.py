import json
import os
from pathlib import Path
import subprocess
import sys

from jusi.plugins.vd import catalog_entry
from jusi.plugins.vd.snapshot import capture
from jusi.plugin_api import validate_discovered_entry


def test_bundled_catalog_and_worker_do_not_import_kernel_or_visidata(tmp_path):
    entry = catalog_entry()
    validate_discovered_entry(entry, entry_point_name="jusi_vd", distribution="jusi", distribution_version=entry["plugin_version"])
    script = '''
import json, sys, tempfile, os
from jusi.kernel_artifacts import publish_json
from jusi.plugins.vd import catalog_entry
from jusi.plugins.vd.worker import create_worker
assert not any(name in sys.modules for name in ('IPython', 'ipykernel', 'visidata', 'pandas'))
worker = create_worker(None)
root = tempfile.TemporaryDirectory(prefix="jusi-artifacts-")
os.environ["JUSI_KERNEL_ARTIFACT_DIRECTORY"] = root.name
reference = publish_json(json.loads(sys.argv[1]))
try:
    result = worker.handle('execute', reference)
    path = result.core_requests[0].argv[-1]
    from pathlib import Path
    assert Path(path).stat().st_mode & 0o777 == 0o600
    assert Path(path).parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(Path(path).read_text()) == json.loads(sys.argv[1])
finally:
    worker.close()
    worker.close()
    root.cleanup()
assert not Path(path).exists()
assert not any(name in sys.modules for name in ('IPython', 'ipykernel', 'visidata', 'pandas'))
'''
    result = subprocess.run([sys.executable, "-c", script, json.dumps(capture([{"value": "α"}]))],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_visidata_key_hooks_capture_before_async_delivery(tmp_path):
    script = '''
from visidata import vd, Sheet, ItemColumn, SequenceSheet
import jusi.editor_client as client
from jusi.plugins.vd.application import install_editor_actions
calls = []
client.copy = lambda text: calls.append(('copy', text))
client.open_text = lambda text, **opts: calls.append(('open', text))
install_editor_actions()
sheet = SequenceSheet('test', rows=[['  α\\nβ  '], ['second']])
sheet.addColumn(ItemColumn('value', 0))
vd.push(sheet, load=False)
assert sheet.getCommand(vd.prettykeys('zY')).longname == 'syscopy-cell'
assert sheet.getCommand(vd.prettykeys('^O')).longname == 'jusi-open-value'
thread = sheet.syscopyValue('  literal\\n ')
thread.join(2)
thread = sheet.jusiOpenValue()
thread.join(2)
assert calls == [('copy', '  literal\\n '), ('open', '  α\\nβ  ')], calls
assert sheet.rows[0][0] == '  α\\nβ  '
from visidata import TextSheet
text_sheet = TextSheet('text', source=['hello'])
vd.push(text_sheet, load=False)
assert text_sheet.getCommand('Ctrl+O').longname == 'jusi-open-value'
'''
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "HOME": str(tmp_path)},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_application_builds_sheets_and_consumes_private_snapshots(tmp_path):
    script = '''
import json, sys
from pathlib import Path
from visidata import vd
from jusi.plugins.vd.application import run_application
from jusi.plugins.vd.snapshot import capture
vd.loadConfigAndPlugins = lambda: None
seen = []
vd.run = lambda sheet: seen.append(sheet)
for value in (None, [{'value': 'hello'}]):
    path = Path(sys.argv[1]) / 'snapshot.json'
    path.write_text(json.dumps(capture(value)))
    assert run_application(path) == 0
    assert not path.exists()
assert all(sheet is not None for sheet in seen)
assert seen[0].source is None
assert seen[1].source == [{'value': 'hello'}]
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
