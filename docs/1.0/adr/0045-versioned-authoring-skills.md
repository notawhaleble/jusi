# ADR 0045: Versioned authoring skills and reference source

Status: accepted

## Decision

Skill templates live in the core repository's `skills/` directory and ship as
Python package data. `jusi install-skills` installs both the exact-plugin and
family skills to an explicit directory or the conventional Codex skills path.

Reference material comes from the canonical Git repository's tag
`v<installed-distribution-version>`. An explicit local Git source can substitute
its committed HEAD, with the same project-version check. This supports offline
source availability and development artifacts without guessing a branch.
The installer records provenance, commit and content hashes. References are
versioned and shared by the two skills, outside the skill-discovery directory.
Each installed skill receives a generated reference-location file.

References are read-only authoring inputs, not editable backend installations.
Skills direct implementation into the requested plugin project and do not apply
core-maintenance instructions indiscriminately to external projects. Python
and Neovim installation remain separate; reference code is not added to editor
runtimepath.

Upgrade validates existing managed content before fetching and again before
replacement. Unmanaged or edited skills/reference files require the user to
preserve or move them; there is no force overwrite. A destination lock prevents
concurrent installers. Staged replacement retains backups until both skills are
installed and rolls back ordinary filesystem failures. A process crash leaves
its lock/staging files for explicit recovery instead of deleting potential
backups. Old versioned references are not automatically pruned.

Tests cover real Git repositories and packaged CLI installation, including
unpublished builds through explicit local source. Structural validation of the
skills does not substitute for an independent external-plugin development test.
