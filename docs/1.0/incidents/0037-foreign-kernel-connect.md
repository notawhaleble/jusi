# Incident 0037 — Reopened notebook selected a foreign kernel

After an editor crash, reopening two Docker-backed notebooks produced ownership
conflicts on target start. A direct connect showed an active kernel but cells
could not execute. A stop appeared to affect the other notebook. The original
endpoint selection and stop sequence were not recorded.

Named target start checked the notebook identity in the health snapshot.
Direct connect skipped that check and selected the inspected kernel ID.
Execute included the fresh notebook ID while stop addressed the kernel by ID
alone. A controller already observing an idle service could also select a
kernel started by another notebook from a state event lacking notebook identity.

All connection paths now reject an active foreign notebook before subscribing.
Kernel state events update only a kernel already selected by an owned snapshot
or command response. The frontend transport controller regression exercises
both paths and checks that stop sends no request for the foreign kernel.
