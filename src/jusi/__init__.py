"""Jusi 1.0 service and protocol package."""
__all__ = ["__version__"]


def __getattr__(name):
    if name == "__version__":
        from importlib.metadata import version
        value = version("jusi")
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
