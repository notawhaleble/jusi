"""Kernel-only expression evaluation and data handoff."""
from jusi import __version__
from .snapshot import capture


def jusi_kernel_adapter_v1():
    return {"plugin_id": "jusi_vd", "plugin_version": __version__,
            "families": [{"family_id": "visidata", "magic_name": "vd"}]}


def load_ipython_extension(ipython):
    from IPython.core.error import UsageError
    from IPython.display import display

    def vd_magic(line, cell):
        if line.strip():
            raise UsageError("%%vd takes a Python expression in its body, without header arguments")
        if not cell.strip():
            raise UsageError("%%vd requires a Python expression")
        value = eval(compile(cell.strip(), "<jusi %%vd>", "eval"), ipython.user_ns, ipython.user_ns)
        snapshot = capture(value)
        display({"application/vnd.jusi.handoff.v1+json": {
            "protocol_version": 1, "kind": "plugin.handoff", "plugin_id": "jusi_vd",
            "plugin_version": __version__, "family_id": "visidata", "magic_name": "vd", "payload": snapshot,
        }}, raw=True)

    ipython.register_magic_function(vd_magic, "cell", "vd")
