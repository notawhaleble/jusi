"""VisiData application process. All keybindings and selections live here."""
import json
import locale
from pathlib import Path
import sys

from .snapshot import Table, restore


def install_editor_actions():
    from visidata import Sheet, VisiData, vd
    from jusi.editor_client import copy, open_text

    def deliver(action, text):
        try:
            if action == "copy":
                copy(text)
                vd.status("copied to editor")
            else:
                open_text(text, name="visidata.txt", filetype="text")
                vd.status("opened in editor")
        except (RuntimeError, ValueError) as exc:
            vd.warning(str(exc))

    def copy_value(sheet, value):
        return vd.execAsync(deliver, "copy", str(value), sheet=None)

    def copy_cells(sheet, cols, rows, filetype=None):
        # Capture displayed values on the invoking thread, before delivery starts.
        text = "\n".join("\t".join(col.getDisplayValue(row) for col in cols) for row in rows)
        return vd.execAsync(deliver, "copy", text, sheet=None)

    def open_value(sheet):
        return vd.execAsync(deliver, "open", str(sheet.cursorValue), sheet=None)

    Sheet.syscopyValue = copy_value
    Sheet.syscopyCells = copy_cells
    Sheet.syscopyCells_async = copy_cells
    Sheet.jusiOpenValue = open_value
    Sheet.addCommand('Ctrl+O', 'jusi-open-value', 'jusiOpenValue()', 'open current value in the Jusi editor')

    @VisiData.before
    def push(vd, vs, *args, **kwargs):
        if isinstance(vs, Sheet):
            # TextSheet and other derived sheets have more specific external-
            # editor bindings. This integration opens snapshots on every sheet.
            vd.bindkey('Ctrl+O', 'jusi-open-value', vs)



def run_application(path):
    # The embedded entry point bypasses VisiData's CLI locale initialization.
    # An inherited Linux C.UTF-8 locale is unavailable on macOS.
    for candidate in ("", "C.UTF-8", "en_US.UTF-8", "UTF-8"):
        try:
            locale.setlocale(locale.LC_CTYPE, candidate)
            if locale.nl_langinfo(locale.CODESET).lower().replace("-", "") == "utf8":
                break
        except locale.Error:
            continue
    else:
        raise RuntimeError("VisiData requires an installed UTF-8 locale")
    from visidata import ItemColumn, SequenceSheet, PyobjSheet, vd
    from visidata.pyobj import PythonAtomSheet

    try:
        with path.open("rb") as stream:
            value = restore(json.load(stream))
    finally:
        path.unlink(missing_ok=True)
    # Avoid competing first-use StoredList writers racing on mkdir.
    Path(str(vd.data_dir)).expanduser().mkdir(parents=True, exist_ok=True)
    vd.loadConfigAndPlugins()
    install_editor_actions()
    if isinstance(value, Table):
        sheet = SequenceSheet("jusi-vd", rows=value.rows)
        sheet.addColumn(*(ItemColumn(name, index) for index, name in enumerate(value.columns)))
        sheet.nKeys = 1
    elif value is None:
        sheet = PythonAtomSheet("jusi-vd", source=None)
    else:
        sheet = PyobjSheet("jusi-vd", source=value)
    vd.run(sheet)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("VisiData application requires one snapshot path")
    raise SystemExit(run_application(Path(sys.argv[1])))
