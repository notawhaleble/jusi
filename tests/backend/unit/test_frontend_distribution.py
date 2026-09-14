from pathlib import Path

import pytest

from jusi import frontend
from jusi.__main__ import main


def test_bundled_frontend_takes_precedence_over_checkout(tmp_path, monkeypatch, capsys):
    package = tmp_path / "src/jusi"
    monkeypatch.setattr(frontend, "__file__", str(package / "frontend.py"))
    (tmp_path / "pyproject.toml").touch()
    for directory in (tmp_path, package / "_frontend"):
        (directory / "plugin").mkdir(parents=True)
        (directory / "plugin/jusi.lua").touch()
    assert main(["frontend-path"]) == 0
    assert Path(capsys.readouterr().out.strip()) == package / "_frontend"


def test_editable_frontend_uses_its_own_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(frontend, "__file__", str(tmp_path / "src/jusi/frontend.py"))
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin/jusi.lua").touch()
    assert frontend.frontend_path() == tmp_path


def test_missing_frontend_is_a_cli_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(frontend, "__file__", str(tmp_path / "jusi/frontend.py"))
    with pytest.raises(SystemExit) as error:
        main(["frontend-path"])
    assert error.value.code == 1
    assert "no Neovim runtime" in capsys.readouterr().err
