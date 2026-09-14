# Jupyter notebook conversion

Convert code-cell sources without starting Neovim, a service, or a kernel:

```sh
jusi import-ipynb analysis.ipynb
# writes analysis.vipynb
jusi export-ipynb analysis.vipynb -o exported.ipynb
```

Omit `-o` to use the input filename with the opposite extension. Existing output
files are refused; add `--force` to replace one. Input and output must be different
files. Conversion validates the whole input before publishing the result.

Import accepts Jupyter nbformat 4 notebooks. Only code-cell source is imported;
Markdown and raw cells are skipped, with a count printed by the command.
Outputs, execution counts, attachments and metadata are not imported.

Export includes each native cell's active body, in order. Followup history and
text outside cells are excluded. The result is an nbformat 4.5 notebook with
empty outputs, null execution counts and no kernel metadata. Magic headers are
preserved literally; conversion does not translate Jusi plugins into Jupyter
extensions.

Unicode, whitespace, empty cells and trailing source newlines are preserved.
CRLF line endings become LF. A code source containing an exact full-line native
delimiter (`╭──`, `╰──`, `╞══`, `├┄┄`) cannot be represented and causes import to
fail with a cell and line number. Indented delimiters are ordinary source.
Malformed native boundaries cause export to fail instead of exporting a partial
notebook. An empty file exports as an empty notebook; nonempty files without
native cells are rejected.

This is source conversion, not a lossless notebook archive. Jupyter nbformat 3
and legacy Jusi `##` notebooks are not supported. See the
[native format](notebook-format.md) and the
[Jupyter format specification](https://nbformat.readthedocs.io/en/latest/format_description.html).
