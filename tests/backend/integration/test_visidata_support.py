import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("override", [False, True])
def test_user_config_and_editor_hooks_at_ui_start(tmp_path, override):
    config = tmp_path / ("custom.py" if override else ".visidatarc")
    config.write_text(
        "options.disp_menu = False\n"
        "options.default_width = 37\n"
        "Sheet.bindkey('Ctrl+O', 'quit-sheet')\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in {"VD_CONFIG", "VD_DIR"}}
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_DATA_HOME=str(tmp_path / "data"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    if override:
        env["VD_CONFIG"] = str(config)
    script = '''
import sys
from jusi.visidata_support import initialize_visidata
assert 'visidata' not in sys.modules
vd = initialize_visidata(open_name='custom.txt')
from visidata import Sheet, TextSheet
assert not vd.lastErrors, vd.lastErrors
assert vd.options.disp_menu is False
assert vd.options.default_width == 37
seen = []
def inspect_start():
    assert vd.options.disp_menu is False
    assert vd.activeSheet.getCommand('Ctrl+O').longname == 'jusi-open-value'
    seen.append(True)
    # Stop before curses initialization, after real vd.run startup hooks.
    raise SystemExit(0)
vd.initCurses = inspect_start
try:
    vd.run(TextSheet('test', source=['hello']))
except SystemExit:
    pass
assert seen == [True]
'''
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
