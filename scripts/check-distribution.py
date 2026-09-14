"""Install a built wheel in isolation and exercise the shipped Neovim runtime.

Run with a Python that has venv support. Dependency installation needs an index
or pip cache. No editable package, checkout runtimepath, or user config is used.
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
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    nvim = shutil.which("nvim")
    if not nvim:
        parser.error("Neovim 0.11+ must be on PATH")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for path in ("lua/jusi/init.lua", "plugin/jusi.lua", "ftdetect/jusi.lua", "ftplugin/jusi.lua"):
            assert "jusi/_frontend/" + path in names, f"Wheel is missing {path}"
        assert not any(name.startswith(("tests/", "docs/", "lua/")) for name in names)
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
        frontend = Path(run([jusi, "frontend-path"], cwd=root, env=env))
        assert frontend.is_relative_to(environment), f"Frontend escaped installed environment: {frontend}"
        run([python, "-c", "import importlib.util; assert importlib.util.find_spec('visidata') is None"], cwd=root, env=env)
        shutil.copyfile(fixture, root / "smoke.lua")
        (root / "init.lua").write_text('''
vim.opt.packpath = { vim.env.VIMRUNTIME }
vim.opt.runtimepath = { vim.env.VIMRUNTIME }
local runtime = vim.fn.system({ "jusi", "frontend-path" })
assert(vim.v.shell_error == 0, runtime)
vim.opt.runtimepath:prepend(vim.trim(runtime))
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
