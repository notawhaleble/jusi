"""Locate the Neovim runtime shipped with this Python distribution."""
from pathlib import Path


def frontend_path() -> Path:
    package = Path(__file__).resolve().parent
    bundled = package / "_frontend"
    if (bundled / "plugin" / "jusi.lua").is_file():
        return bundled
    # Editable installations use the same checkout as the Python package.
    checkout = package.parent.parent
    if (checkout / "pyproject.toml").is_file() and (checkout / "plugin" / "jusi.lua").is_file():
        return checkout
    raise RuntimeError("The installed Jusi package has no Neovim runtime; reinstall a complete Jusi 1.0 wheel")
