"""Source-only conversion between nbformat 4 and native Jusi notebooks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

OPEN, CLOSE, HISTORY, SEPARATOR = "╭──", "╰──", "╞══", "├┄┄"
RESERVED = {OPEN, CLOSE, HISTORY, SEPARATOR}


class ConversionError(ValueError):
    pass


def import_ipynb(notebook):
    if not isinstance(notebook, dict) or notebook.get("nbformat") != 4:
        raise ConversionError("Expected a Jupyter nbformat 4 notebook")
    cells = notebook.get("cells")
    if not isinstance(cells, list):
        raise ConversionError("Notebook cells must be an array")
    blocks = []
    for index, cell in enumerate(cells, 1):
        if not isinstance(cell, dict) or not isinstance(cell.get("cell_type"), str):
            raise ConversionError(f"Cell {index} has no valid cell_type")
        if cell["cell_type"] not in {"code", "markdown", "raw"}:
            raise ConversionError(f"Cell {index} has unsupported cell_type: {cell['cell_type']}")
        source = cell.get("source")
        if isinstance(source, list) and all(isinstance(part, str) for part in source):
            source = "".join(source)
        if not isinstance(source, str):
            raise ConversionError(f"Cell {index} source must be a string or array of strings")
        source = source.replace("\r\n", "\n")
        for row, line in enumerate(source.split("\n"), 1):
            if line in RESERVED:
                raise ConversionError(f"Cell {index}, source line {row}: literal {line} conflicts with a native delimiter")
        # An extra separator newline before CLOSE keeps trailing source newlines
        # as body rows rather than accidentally removing one during conversion.
        blocks.append(OPEN + "\n" + source + "\n" + CLOSE)
    return "\n\n".join(blocks) + ("\n" if blocks else ""), len(blocks), 0


def export_ipynb(text):
    cells = []
    body = None
    history = False
    for row, line in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        def invalid(message):
            raise ConversionError(f"Native line {row}: {message}")
        if line == OPEN:
            if body is not None:
                invalid("new opener before previous cell closes")
            body, history = [], False
        elif line == CLOSE:
            if body is None:
                invalid("closer without an opener")
            cells.append({"cell_type": "code", "id": f"cell-{len(cells) + 1}", "metadata": {},
                          "source": "\n".join(body), "execution_count": None, "outputs": []})
            body = None
        elif line == HISTORY:
            if body is None or history:
                invalid("history boundary outside a cell or repeated")
            history = True
        elif line == SEPARATOR:
            if body is None or not history:
                invalid("history separator before a history region")
        elif body is not None and not history:
            body.append(line)
    if body is not None:
        raise ConversionError("Native notebook ends inside an unclosed cell")
    if not cells and text.strip():
        raise ConversionError("No native Jusi cells found")
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}


def convert_file(input_path, output_path=None, *, direction, force=False):
    if direction not in {"import", "export"}:
        raise ConversionError("Unknown conversion direction")
    source = Path(input_path)
    target = Path(output_path) if output_path else source.with_suffix(".vipynb" if direction == "import" else ".ipynb")
    if source.resolve() == target.resolve():
        raise ConversionError("Input and output must be different files")
    with source.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    if direction == "import":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ConversionError(f"Invalid notebook JSON: {exc}") from exc
        result, count, skipped = import_ipynb(data)
    elif direction == "export":
        data = export_ipynb(text)
        result, count, skipped = json.dumps(data, ensure_ascii=False, indent=2) + "\n", len(data["cells"]), 0
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent,
                                         prefix=".jusi-convert-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(result)
        if force:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise ConversionError(f"Output already exists: {target}; use --force to replace it") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target, count, skipped
