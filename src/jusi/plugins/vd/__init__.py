"""VisiData provider catalog. Runtime imports stay in their owning processes."""
from jusi import __version__


def catalog_entry():
    return {
        "plugin_id": "jusi_vd", "plugin_version": __version__, "distribution": "jusi",
        "families": [{"family_id": "visidata", "magic_name": "vd",
                      "capabilities": ["execute", "editor_actions"],
                      "presentation": {"syntax": "python", "indent": "python"}}],
        "kernel_extensions": ["jusi.plugins.vd.kernel"],
        "worker_entry_point": "jusi.plugins.vd.worker:create_worker",
        "media_types": ["text/x-ansi"], "interaction": "terminal_interactive",
    }
