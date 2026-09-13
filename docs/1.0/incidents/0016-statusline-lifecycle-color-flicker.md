# Incident 0016: Statusline lifecycle color flicker

## Symptom

Start and stop flashed inconsistent backgrounds, including greenish off and red
off. Successful shutdown then turned the entire statusline gray and replaced
off with unknown.

## Cause

Kernel badges reused diagnostic groups, inheriting colorscheme-specific
backgrounds. Disconnection switched the group from error to warning regardless
of the retained off value. A later styling change applied the unknown-state
background to the whole statusline. Finally, session retirement erased the
controller reference used to render the last observed off value. Composed start
also exposed its intermediate service-only inspection as an ordinary off view.

## Correction

Explicit muted state palettes apply only to the kernel badge. The surrounding
statusline uses native active/inactive theme groups. Off and unknown share gray;
confirmed on is green; stale state is amber. A buffer-owned display snapshot
preserves observed off after successful stop without retaining a live resource.
Start and stop are separate operation labels. The composed start view retains
its prior off/unknown value until on is confirmed or startup ends. Teardown does
not recolor off.

Frontend tests cover the badge reset, retained off, startup connection phase and
stop teardown. The target lifecycle end-to-end scenario verifies the final off
view after its owned service and controller have been removed.
