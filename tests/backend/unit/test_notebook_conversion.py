import json

import pytest

from jusi.__main__ import main
from jusi.notebook_conversion import ConversionError, convert_file, export_ipynb, import_ipynb


def notebook(*sources):
    return {"nbformat": 4, "cells": [{"cell_type": "code", "source": source,
            "execution_count": 42, "outputs": [{"text": "discard"}]} for source in sources]}


def test_source_roundtrip_and_no_outputs():
    sources = ["", "\n", "x\n\n", "\tα = '你好'  ", "%%vd\ndata", "x\n\ny", "x\u2028y", " ╭──"]
    native, count, skipped = import_ipynb(notebook(*sources))
    result = export_ipynb(native)
    assert (count, skipped) == (len(sources), 0)
    assert [cell["source"] for cell in result["cells"]] == sources
    assert len({cell["id"] for cell in result["cells"]}) == len(sources)
    assert all(cell["outputs"] == [] and cell["execution_count"] is None for cell in result["cells"])
    assert result["metadata"] == {}


def test_all_cell_types_preserve_order_and_newlines():
    data = notebook(["im", "port os\r\n", "print(os)"])
    data["cells"].insert(0, {"cell_type": "markdown", "source": "# Heading"})
    data["cells"].append({"cell_type": "raw", "source": ["raw", " text"]})
    text, count, skipped = import_ipynb(data)
    assert (count, skipped) == (3, 0)
    assert [cell["source"] for cell in export_ipynb(text)["cells"]] == [
        "# Heading", "import os\nprint(os)", "raw text"]


def test_export_active_bodies_only():
    result = export_ipynb("prose\n╭──\n%%fixture\ncurrent\n╞══\nold\n├┄┄\nolder\n╰──\n╭──\n╰──\nnotes")
    assert [cell["source"] for cell in result["cells"]] == ["%%fixture\ncurrent", ""]
    assert export_ipynb("")["cells"] == []
    assert import_ipynb(notebook()) == ("", 0, 0)


@pytest.mark.parametrize("text", ["╭──\nx", "╰──", "╞══", "├┄┄", "plain text",
    "╭──\n╭──\n╰──", "╭──\n├┄┄\n╰──", "╭──\n╞══\n╞══\n╰──"])
def test_malformed_native_rejected(text):
    with pytest.raises(ConversionError):
        export_ipynb(text)


@pytest.mark.parametrize("line", ["╭──", "╰──", "╞══", "├┄┄"])
def test_unrepresentable_source_rejected(line):
    with pytest.raises(ConversionError, match="Cell 1, source line 2"):
        import_ipynb(notebook("before\n" + line + "\nafter"))


@pytest.mark.parametrize("data", [None, {}, {"nbformat": 3}, {"nbformat": 4, "cells": {}},
    {"nbformat": 4, "cells": [None]}, notebook(None), notebook(["a", 1])])
def test_invalid_jupyter_rejected(data):
    with pytest.raises(ConversionError):
        import_ipynb(data)


def test_cli_defaults_override_and_force(tmp_path, capsys):
    source = tmp_path / "notebook.ipynb"
    source.write_text(json.dumps(notebook("x\n")), encoding="utf-8")
    assert main(["import-ipynb", str(source)]) == 0
    native = source.with_suffix(".vipynb")
    assert "1 cells" in capsys.readouterr().out
    original = source.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["export-ipynb", str(native)])
    assert exc.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert source.read_bytes() == original
    assert main(["export-ipynb", str(native), "--force"]) == 0
    assert json.loads(source.read_text())["cells"][0]["source"] == "x\n"
    target = tmp_path / "custom name.vipynb"
    assert main(["import-ipynb", str(source), "-o", str(target)]) == 0
    assert target.read_bytes() == native.read_bytes()
    assert not list(tmp_path.glob(".jusi-convert-*"))


def test_failed_conversion_preserves_files(tmp_path):
    source, target = tmp_path / "bad.ipynb", tmp_path / "saved.vipynb"
    source.write_text("invalid json")
    target.write_text("keep me")
    with pytest.raises(ConversionError, match="Invalid notebook JSON"):
        convert_file(source, target, direction="import", force=True)
    assert target.read_text() == "keep me"
    with pytest.raises(ConversionError, match="different files"):
        convert_file(source, source, direction="import", force=True)
    assert source.read_text() == "invalid json"
    assert not list(tmp_path.glob(".jusi-convert-*"))


@pytest.mark.parametrize("kind", ["markdown", "raw"])
def test_non_code_only_notebook_imports_as_ordinary_cells(kind):
    data = {"nbformat": 4, "cells": [{"cell_type": kind, "source": "α\n\n"},
                                    {"cell_type": kind, "source": ""}]}
    text, count, skipped = import_ipynb(data)
    assert (count, skipped) == (2, 0)
    assert text == "╭──\nα\n\n\n╰──\n\n╭──\n\n╰──\n"
    assert [cell["source"] for cell in export_ipynb(text)["cells"]] == ["α\n\n", ""]
