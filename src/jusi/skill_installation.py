"""Install managed authoring skills and a version-matched reference checkout."""
from __future__ import annotations

import hashlib
from importlib.metadata import version as distribution_version
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

NAMES = ("jusi-plugin-development", "jusi-plugin-family-development")
REPOSITORY = "https://github.com/notawhaleble/jusi.git"
MANIFEST = ".jusi-install.json"


class SkillInstallationError(RuntimeError):
    pass


def git(*args, cwd=None):
    try:
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *map(str, args)],
                                cwd=cwd, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillInstallationError(f"Cannot obtain reference source: {exc}") from exc
    if result.returncode:
        raise SkillInstallationError(f"Git reference operation failed: {result.stderr.strip()}")
    return result.stdout.strip()


def hashes(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            raise SkillInstallationError(f"Refusing symlink in managed content: {path}")
        if path.is_file() and relative.as_posix() != MANIFEST:
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def managed(root, kind):
    if root.is_symlink() or not root.is_dir():
        raise SkillInstallationError(f"Not a managed Jusi directory: {root}")
    try:
        manifest = json.loads((root / MANIFEST).read_text())
    except (OSError, ValueError) as exc:
        raise SkillInstallationError(f"Existing directory is unmanaged; choose another destination or move it aside: {root}") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != kind or manifest.get("files") != hashes(root):
        raise SkillInstallationError(f"Managed content was edited; preserve or move it aside before reinstalling: {root}")
    return manifest


def record(root, kind, **metadata):
    (root / MANIFEST).write_text(json.dumps({"kind": kind, **metadata, "files": hashes(root)}, indent=2) + "\n")


def templates():
    package = Path(__file__).resolve().parent
    bundled = package / "_skills"
    if bundled.is_dir():
        return bundled
    checkout = package.parent.parent / "skills"
    if checkout.is_dir():
        return checkout
    raise SkillInstallationError("Authoring skills are missing from this Jusi installation")


def install_skills(*, directory=None, source=None):
    version = distribution_version("jusi")
    destination = Path(directory).expanduser() if directory else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    lock = destination / ".jusi-install.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise SkillInstallationError(f"Another install is active, or its lock remains after interruption: {lock}") from exc
    stage = None
    keep_stage = False
    try:
        # Validate both installed skills before fetching or replacing anything.
        for name in NAMES:
            current = destination / name
            if current.exists() or current.is_symlink():
                managed(current, "skill")
        origin = str(Path(source).expanduser().resolve()) if source else REPOSITORY
        revision = git("rev-parse", "HEAD", cwd=origin) if source else f"v{version}"
        key = hashlib.sha256(f"{version}\n{origin}\n{revision}".encode()).hexdigest()[:20]
        references = destination.parent / "jusi-references"
        if references.is_symlink():
            raise SkillInstallationError(f"Reference directory must not be a symlink: {references}")
        references.mkdir(exist_ok=True)
        reference = references / f"{version}-{key}"
        stage = Path(tempfile.mkdtemp(prefix=".jusi-stage-", dir=destination))
        if reference.exists() or reference.is_symlink():
            metadata = managed(reference, "reference")
            if (metadata.get("version"), metadata.get("origin"), metadata.get("revision")) != (version, origin, revision):
                raise SkillInstallationError(f"Reference provenance does not match: {reference}")
            commit = git("rev-parse", "HEAD", cwd=reference)
            if commit != metadata.get("commit"):
                raise SkillInstallationError(f"Reference Git revision was changed: {reference}")
        else:
            clone = stage / "reference"
            if source:
                git("clone", "--quiet", "--no-local", "--", origin, clone)
                git("checkout", "--quiet", "--detach", revision, cwd=clone)
            else:
                git("clone", "--quiet", "--depth", "1", "--branch", revision, "--", origin, clone)
            try:
                project = tomllib.loads((clone / "pyproject.toml").read_text())["project"]
                actual = project["version"]
                if project.get("name") != "jusi":
                    raise SkillInstallationError("Reference source is not the Jusi project")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise SkillInstallationError("Reference source has no readable Jusi project version") from exc
            if actual != version:
                raise SkillInstallationError(f"Reference is Jusi {actual}, installed backend is {version}")
            for required in ("AGENTS.md", "src/jusi/plugin_api.py", "protocol/schema/v1/plugin-catalog.schema.json", "docs/1.0/invariants.md"):
                if not (clone / required).is_file():
                    raise SkillInstallationError(f"Reference source is missing {required}")
            commit = git("rev-parse", "HEAD", cwd=clone)
            record(clone, "reference", version=version, origin=origin, revision=revision, commit=commit)
            clone.rename(reference)
        for name in NAMES:
            prepared = stage / name
            shutil.copytree(templates() / name, prepared)
            (prepared / "references/source.md").write_text(
                f"# Installed Jusi reference\n\nVersion: {version}\n\nGit commit: {commit}\n\n"
                f"Reference root: `{reference}`\n\n"
                "All core paths in this skill are relative to this root. Treat it as read-only; "
                "create and test the requested plugin in the user's project. The reference is "
                "not an editable backend installation. Do not run git pull or modify it. "
                "After upgrading Jusi, rerun `jusi install-skills` to install matching references.\n")
            record(prepared, "skill", version=version, commit=commit, reference=str(reference))
        # Keep old directories until both replacements succeed; roll back on
        # an ordinary filesystem failure. Unknown or edited files never enter here.
        backups, installed = [], []
        try:
            for name in NAMES:
                current = destination / name
                if current.exists() or current.is_symlink():
                    managed(current, "skill")
                    backup = stage / (name + ".previous")
                    current.rename(backup)
                    backups.append((backup, current))
                (stage / name).rename(current)
                installed.append(current)
        except BaseException:
            try:
                for current in reversed(installed):
                    shutil.rmtree(current)
                for backup, current in reversed(backups):
                    backup.rename(current)
            except BaseException as rollback_error:
                keep_stage = True
                raise SkillInstallationError(f"Rollback could not finish; preserve and restore backups in {stage} before removing {lock}") from rollback_error
            raise
        return {"directory": str(destination), "reference": str(reference), "version": version, "commit": commit}
    finally:
        if not keep_stage:
            if stage is not None:
                shutil.rmtree(stage)
            lock.rmdir()
