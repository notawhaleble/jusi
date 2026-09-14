# Plugin-development skills

Jusi ships two authoring skills:

- `jusi-plugin-development`: exact-provider plugins, workers, terminal clients,
  followups, completions, interruption and editor actions.
- `jusi-plugin-family-development`: shared magic semantics, alias resolution,
  provider registration and coexistence.

Install Jusi in your development environment, make Git available, then run:

```sh
jusi install-skills
```

The command installs both skills into `$CODEX_HOME/skills`, or `~/.codex/skills`
when `CODEX_HOME` is unset. It downloads the matching `v<installed-version>` tag
from the Jusi GitHub repository and keeps a reference checkout in the adjacent
`jusi-references` directory. The skills' `references/source.md` records its
absolute path, package version and Git commit. You do not need an existing core
checkout. The Python package supplies the skill templates; the reference gives
the agent the corresponding API, contracts, fixtures and examples.

Start a new agent session in your plugin project so it discovers the installed
skills. For example:

```text
Use $jusi-plugin-development to build a %%todo plugin in this project.
```

Supply the behavior you want for that plugin. The skill treats the core checkout
as read-only reference and keeps implementation and tests in your project.
Core changes require their own task scope. The reference is separate from the
skills directory so its own source templates do not become duplicate skills.

To use another compatible skills directory:

```sh
jusi install-skills --directory /path/to/agent/skills
```

## Updates and customizations

After upgrading Jusi, rerun `jusi install-skills`. Unmodified managed skills are
replaced together and point to a matching versioned checkout. Old references
remain available. Repeating the command for the same release reuses the verified
checkout without fetching it again, including offline.

The installer checks content hashes before replacement. Existing unmanaged
skills, edited files, extra files and symlinks are not overwritten. Preserve your
customization elsewhere or move the conflicting directory outside the skills
search path before reinstalling. Alternatively choose a new destination. There
is no silent force-replacement mode.

Do not edit or pull inside a managed reference checkout. If you need to change
core, use a separate working checkout. A modified reference is reported as a
conflict; preserve or move it aside before reinstalling.

An interrupted installation can leave `.jusi-install.lock` and a
`.jusi-stage-*` directory in the destination. Stop any running installer first.
Inspect the staging directory for `.previous` skill backups and restore or
preserve them before removing the stale lock and retrying. Replacement errors trigger rollback. If rollback itself fails, backups and the
lock remain for manual recovery.

## Local source and maintainer verification

For offline installation from an available repository, or development builds:

```sh
jusi install-skills --source /path/to/jusi --directory /tmp/jusi-agent/skills
```

This clones the specified repository's committed HEAD, not its uncommitted
files. Its project version must match the installed backend. Both source choices
record the exact commit and verify cached contents on reuse.

`tests/backend/integration/test_skill_installation.py` covers release-tag fetch,
offline reuse, upgrades, user edits, version mismatch, replacement rollback and
concurrent edits during a fetch. The [distribution gate](architecture/distribution-verification.md)
also installs the packaged skills into an isolated directory and verifies repeat
installation and edit protection using the wheel's CLI.

These checks validate distribution and installation. Independent skill behavior
is tested by developing a separate plugin with the installed skill; successful
installation alone does not establish plugin quality.
