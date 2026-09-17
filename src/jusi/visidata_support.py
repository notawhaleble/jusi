"""Optional startup helpers for target-side VisiData applications.

Importing this module does not import VisiData. Call initialize_visidata once
in the terminal application, before constructing application sheets.
"""
import locale
from pathlib import Path


def initialize_visidata(*, open_name="visidata.txt", open_filetype="text"):
    """Load normal user configuration, then install Jusi editor integration.

    Returns VisiData's application object. The caller owns sheet construction,
    application commands and the eventual vd.run(*sheets) call.
    """
    # Embedded applications bypass the CLI's UTF-8 locale initialization.
    for candidate in ("", "C.UTF-8", "en_US.UTF-8", "UTF-8"):
        try:
            locale.setlocale(locale.LC_CTYPE, candidate)
            if locale.nl_langinfo(locale.CODESET).lower().replace("-", "") == "utf8":
                break
        except locale.Error:
            continue
    else:
        raise RuntimeError("VisiData requires an installed UTF-8 locale")

    from visidata import vd

    # Avoid concurrent first-use StoredList writers racing on mkdir.
    Path(str(vd.data_dir)).expanduser().mkdir(parents=True, exist_ok=True)
    vd.loadConfigAndPlugins()
    install_editor_actions(open_name=open_name, open_filetype=open_filetype)
    return vd


def install_editor_actions(*, open_name="visidata.txt", open_filetype="text"):
    """Route VisiData clipboard and open commands through the owning client."""
    from visidata import Sheet, VisiData, vd
    from jusi.editor_client import copy, open_text

    def deliver(action, text):
        try:
            if action == "copy":
                copy(text)
                vd.status("copied to editor")
            else:
                open_text(text, name=open_name, filetype=open_filetype)
                vd.status("opened in editor")
        except (RuntimeError, ValueError) as exc:
            vd.warning(str(exc))

    def copy_value(sheet, value):
        return vd.execAsync(deliver, "copy", str(value), sheet=None)

    def copy_cells(sheet, cols, rows, filetype=None):
        # Capture displayed values before asynchronous delivery.
        text = "\n".join("\t".join(col.getDisplayValue(row) for col in cols) for row in rows)
        return vd.execAsync(deliver, "copy", text, sheet=None)

    def open_value(sheet):
        return vd.execAsync(deliver, "open", str(sheet.cursorValue), sheet=None)

    Sheet.syscopyValue = copy_value
    Sheet.syscopyCells = copy_cells
    Sheet.syscopyCells_async = copy_cells
    Sheet.jusiOpenValue = open_value
    Sheet.addCommand("Ctrl+O", "jusi-open-value", "jusiOpenValue()", "open current value in the Jusi editor")

    @VisiData.before
    def push(vd, vs, *args, **kwargs):
        if isinstance(vs, Sheet):
            # Derived sheets can have more specific external-editor bindings.
            vd.bindkey("Ctrl+O", "jusi-open-value", vs)
