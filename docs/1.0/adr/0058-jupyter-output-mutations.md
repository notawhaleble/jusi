# Jupyter output mutations

The target emits an ordered execution-scoped clear event carrying Jupyter's
`wait` flag. An immediate clear removes the owning cell's output. A deferred
clear keeps it until another output is appended. A chunked display becomes
visible after its complete bundle arrives. Input prompts and display updates
do not consume a pending clear.

Identified displays travel as bounded bundles. The service derives a fixed-size
opaque display ID from the kernel's transient ID. Each transfer has a separate
bundle ID. An update from an active execution replaces every retained occurrence
of its display ID in the same kernel generation. A cleared, closed or replaced
occurrence is forgotten. Existing cell parking and retention rules apply.

Neovim owns output order and rendering. It retains text segments so it can replay
the affected cell's terminal when replacement changes its text. Long stream
segments spill to a temporary file owned by the output area. Rich surfaces are
replaced individually so updating a figure does not execute unrelated JavaScript.
Clearing or retiring the area releases its text, temporary file and rich surfaces.
