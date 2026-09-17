"""Build snapshot sheets in a terminal process using shared VisiData startup."""
import json
from pathlib import Path
import sys

from .snapshot import Table, restore
from jusi.visidata_support import initialize_visidata, install_editor_actions


def run_application(path):
    try:
        with path.open("rb") as stream:
            value = restore(json.load(stream))
    finally:
        path.unlink(missing_ok=True)
    vd = initialize_visidata()
    from visidata import ItemColumn, SequenceSheet, PyobjSheet
    from visidata.pyobj import PythonAtomSheet

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
