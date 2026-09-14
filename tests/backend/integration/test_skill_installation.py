from pathlib import Path

import pytest

from jusi import skill_installation as installer


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "distribution_version", lambda _: "1.0.0")
    root = tmp_path / "source"
    root.mkdir()
    installer.git("init", "--quiet", root)
    for name in ("AGENTS.md", "src/jusi/plugin_api.py", "protocol/schema/v1/plugin-catalog.schema.json", "docs/1.0/invariants.md"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("reference\n")
    commit(root, "1.0.0")
    return root


def commit(root, version):
    (root / "pyproject.toml").write_text(f'[project]\nname = "jusi"\nversion = "{version}"\n')
    installer.git("add", ".", cwd=root)
    installer.git("-c", "user.name=Jusi Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", version, cwd=root)
    installer.git("-c", "tag.gpgsign=false", "tag", "v" + version, cwd=root)


def test_install_repeat_and_upgrade_keep_versioned_references(source, tmp_path, monkeypatch):
    destination = tmp_path / "agent" / "skills"
    first = installer.install_skills(directory=destination, source=source)
    assert installer.install_skills(directory=destination, source=source) == first
    old = Path(first["reference"])
    assert not old.is_relative_to(destination)
    for name in installer.NAMES:
        path = destination / name
        assert str(old) in (path / "references/source.md").read_text()
        assert "/Users/niku" not in (path / "SKILL.md").read_text()
        installer.managed(path, "skill")
    commit(source, "1.0.1")
    monkeypatch.setattr(installer, "distribution_version", lambda _: "1.0.1")
    second = installer.install_skills(directory=destination, source=source)
    assert first["reference"] != second["reference"]
    assert old.is_dir()
    assert installer.managed(destination / installer.NAMES[0], "skill")["version"] == "1.0.1"


def test_release_tag_cache_works_without_origin(source, tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "REPOSITORY", source.as_uri())
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "agent"))
    first = installer.install_skills()
    source.rename(tmp_path / "offline-origin")
    assert installer.install_skills() == first


@pytest.mark.parametrize("target", ["skill", "reference", "unmanaged", "manifest"])
def test_refuses_to_overwrite_edits(source, tmp_path, target):
    destination = tmp_path / "skills"
    result = installer.install_skills(directory=destination, source=source)
    path = destination / installer.NAMES[0] / "SKILL.md"
    if target == "reference":
        path = Path(result["reference"]) / "src/jusi/plugin_api.py"
    elif target == "unmanaged":
        (destination / installer.NAMES[0] / installer.MANIFEST).unlink()
    if target == "manifest":
        (destination / installer.NAMES[0] / installer.MANIFEST).write_text("[]")
    path.write_text("user changes")
    before = (destination / installer.NAMES[1] / "SKILL.md").read_bytes()
    with pytest.raises(installer.SkillInstallationError):
        installer.install_skills(directory=destination, source=source)
    assert path.read_text() == "user changes"
    assert (destination / installer.NAMES[1] / "SKILL.md").read_bytes() == before
    assert not (destination / ".jusi-install.lock").exists()


def test_version_mismatch_does_not_install_skills(source, tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "distribution_version", lambda _: "2.0.0")
    destination = tmp_path / "skills"
    with pytest.raises(installer.SkillInstallationError, match="installed backend is 2.0.0"):
        installer.install_skills(directory=destination, source=source)
    assert not any((destination / name).exists() for name in installer.NAMES)


def test_replacement_failure_restores_both_skills(source, tmp_path, monkeypatch):
    destination = tmp_path / "skills"
    installer.install_skills(directory=destination, source=source)
    before = {name: installer.hashes(destination / name) for name in installer.NAMES}
    rename = Path.rename
    def fail_second(path, target):
        if path.name == installer.NAMES[1] and path.parent.name.startswith(".jusi-stage-"):
            raise OSError("simulated replacement failure")
        return rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError, match="simulated"):
        installer.install_skills(directory=destination, source=source)
    assert before == {name: installer.hashes(destination / name) for name in installer.NAMES}


def test_rechecks_edits_made_during_fetch(source, tmp_path, monkeypatch):
    destination = tmp_path / "skills"
    installer.install_skills(directory=destination, source=source)
    commit(source, "1.0.1")
    monkeypatch.setattr(installer, "distribution_version", lambda _: "1.0.1")
    path = destination / installer.NAMES[0] / "SKILL.md"
    git = installer.git
    def edit_during_clone(*args, **kwargs):
        result = git(*args, **kwargs)
        if args[0] == "clone":
            path.write_text("concurrent user edit")
        return result
    monkeypatch.setattr(installer, "git", edit_during_clone)
    with pytest.raises(installer.SkillInstallationError, match="edited"):
        installer.install_skills(directory=destination, source=source)
    assert path.read_text() == "concurrent user edit"


def test_lock_and_symlink_are_not_overwritten(source, tmp_path):
    destination = tmp_path / "skills"
    destination.mkdir()
    lock = destination / ".jusi-install.lock"
    lock.mkdir()
    with pytest.raises(installer.SkillInstallationError, match="lock"):
        installer.install_skills(directory=destination, source=source)
    lock.rmdir()
    (destination / installer.NAMES[0]).symlink_to(source, target_is_directory=True)
    with pytest.raises(installer.SkillInstallationError, match="managed"):
        installer.install_skills(directory=destination, source=source)
    assert (source / "pyproject.toml").is_file()


def test_failed_rollback_preserves_backups_for_recovery(source, tmp_path, monkeypatch):
    destination = tmp_path / "skills"
    installer.install_skills(directory=destination, source=source)
    before = {name: (destination / name / "SKILL.md").read_bytes() for name in installer.NAMES}
    rename = Path.rename
    def fail_replacement_and_restore(path, target):
        if path.name.endswith(".previous") or (path.name == installer.NAMES[1] and path.parent.name.startswith(".jusi-stage-")):
            raise OSError("filesystem unavailable")
        return rename(path, target)
    monkeypatch.setattr(Path, "rename", fail_replacement_and_restore)
    with pytest.raises(installer.SkillInstallationError, match="restore backups"):
        installer.install_skills(directory=destination, source=source)
    stage, = destination.glob(".jusi-stage-*")
    assert (destination / ".jusi-install.lock").is_dir()
    for name, content in before.items():
        assert (stage / (name + ".previous") / "SKILL.md").read_bytes() == content
