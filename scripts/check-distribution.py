"""Install a built wheel in isolation and exercise a separately Git-installed Neovim runtime.

Run with a Python that has venv support. Dependency installation needs an index
or pip cache. No editable Python package, development runtimepath, or user config is used.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import venv
import zipfile


def run(args, *, cwd, env, timeout=180):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args}\n{result.stdout}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--frontend-repo", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--frontend-ref", default="HEAD")
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    nvim = shutil.which("nvim")
    if not nvim:
        parser.error("Neovim 0.11+ must be on PATH")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert not any(name.startswith(("jusi/_frontend/", "tests/", "docs/", "lua/")) for name in names)
    fixture = Path(__file__).resolve().parents[1] / "tests/packaging/smoke.lua"
    with tempfile.TemporaryDirectory(prefix="jusi installed-") as temporary:
        root = Path(temporary).resolve()
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        home = root / "home"
        home.mkdir()
        env = {key: value for key, value in os.environ.items()
               if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"} and not key.startswith("JUSI_")}
        env.update(HOME=str(home), VIRTUAL_ENV=str(environment), PYTHONNOUSERSITE="1",
                   PATH=str(environment / "bin") + os.pathsep + os.environ.get("PATH", ""),
                   XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data"),
                   XDG_STATE_HOME=str(home / "state"), XDG_CACHE_HOME=str(home / "cache"),
                   IPYTHONDIR=str(home / "ipython"), JUPYTER_RUNTIME_DIR=str(home / "jupyter"))
        python = str(environment / "bin/python")
        jusi = str(environment / "bin/jusi")
        run([python, "-m", "pip", "install", str(wheel)], cwd=root, env=env, timeout=300)
        print(run([jusi, "--version"], cwd=root, env=env), flush=True)
        frontend = home / "data/nvim/site/pack/jusi/start/jusi"
        frontend.parent.mkdir(parents=True)
        run(["git", "clone", "--quiet", "--no-local", "--", args.frontend_repo, str(frontend)], cwd=root, env=env)
        run(["git", "checkout", "--detach", args.frontend_ref], cwd=frontend, env=env)
        skills = root / "agent/skills"
        # The wheel supplies the skills; a committed source clone supplies their
        # matching API/docs/fixtures. This path also works before a release tag exists.
        source = frontend
        install = [jusi, "install-skills", "--directory", str(skills), "--source", str(source)]
        run(install, cwd=root, env=env)
        run(install, cwd=root, env=env)
        for name in ("jusi-plugin-development", "jusi-plugin-family-development"):
            assert (skills / name / "SKILL.md").is_file()
            assert str(root / "agent/jusi-references") in (skills / name / "references/source.md").read_text()
            assert str(source) not in (skills / name / "SKILL.md").read_text()
        edited = skills / "jusi-plugin-development/SKILL.md"
        edited.write_text(edited.read_text() + "\nUser customization\n")
        conflict = subprocess.run(install, cwd=root, env=env, capture_output=True, text=True, timeout=180)
        assert conflict.returncode != 0 and "edited" in conflict.stderr
        assert edited.read_text().endswith("User customization\n")
        print("Wheel-installed skills, matching reference and edit protection passed.", flush=True)
        run([python, "-c", "import importlib.util; assert importlib.util.find_spec('visidata') is None"], cwd=root, env=env)
        shutil.copyfile(fixture, root / "smoke.lua")
        (root / "init.lua").write_text('''
vim.opt.packpath = { vim.fn.stdpath("data") .. "/site", vim.env.VIMRUNTIME }
vim.opt.runtimepath = { vim.env.VIMRUNTIME }
''', encoding="utf-8")
        for with_vd in (False, True):
            if with_vd:
                run([python, "-m", "pip", "install", str(wheel) + "[vd]"], cwd=root, env=env, timeout=300)
            run([python, "-m", "pip", "check"], cwd=root, env=env)
            env["JUSI_SMOKE_VD"] = "1" if with_vd else "0"
            print(run([nvim, "--headless", "-i", "NONE", "-u", str(root / "init.lua"),
                       "-l", str(root / "smoke.lua")], cwd=root, env=env), flush=True)
        print("Installed base and vd distributions passed outside the checkout.", flush=True)


if __name__ == "__main__":
    main()
