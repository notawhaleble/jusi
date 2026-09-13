# Incident 0015: Inherited gutters reduced initial terminal width

## Symptom

A freshly opened VisiData client left a narrow uncovered strip on the right.
Rearranging its split made the application fill the available width.

## Reproduction and cause

The terminal fixture with number, relativenumber, signcolumn=yes and foldcolumn=1
reported `initial=73x12` in an 80-column, 12-row window. Its first-geometry
assertion failed. New output splits inherited notebook window gutters, and the
terminal PTY was created using that reduced text width. A later layout resize
could mask the initial mismatch. Clean-config tests had no gutters, so they did
not expose the defect.

## Fix and verification

Jusi terminal projection windows clear number, relativenumber, signcolumn,
foldcolumn and statuscolumn before interactive job creation. The same setup
applies when revealing an existing output window. Notebook window options remain
unchanged; bridge and target geometry continue to follow native terminal sizes.

The terminal end-to-end scenario now enables notebook gutters, checks that the
target's first reported size equals the full output split dimensions, and verifies
that notebook gutters survive. This uses the generic terminal fixture rather
than any VisiData-specific rendering code.
