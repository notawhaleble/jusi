# Target-Side Configuration

Jusi 1.0 backend and plugin configuration belongs to the target that runs the
service, kernel, and workers. The default path is:

```text
~/.jusi/jusi.toml
```

An alternate target-side path is explicit:

```sh
jusi serve --config /path/to/jusi.toml
```

A missing default file is an empty configuration. A missing explicit file,
malformed TOML, a file larger than 1 MiB, or a TOML value that cannot be carried
as data fails kernel start/restart without stopping the service. Failure details
include the path and parser location when available, never file contents.

The file is read afresh for every kernel start and full notebook restart. The
resulting private snapshot remains fixed for that notebook-runtime generation.
It is not returned by health, sent through SSE, logged, or transmitted to
Neovim.

For a remote shared target, keep this file scoped to capabilities and routing
needed on that target. Prefer provider support for environment-variable or
secret-store resolution over literal credentials. Jusi cannot protect secrets
from administrators or processes that already have equivalent OS authority on
the target.

Plugin families own their sections. For example, the intended SQL shape remains:

```toml
[sql.main]
provider = "sqlite"
path = "/data/main.sqlite"
```

SQL interpretation is not implemented in core. This example documents the
family boundary that the first SQL fixture and later external providers will
consume.

## Palette aliases

At kernel start/restart the service publishes discovered magic names and alias
names from their matching `[magic.alias]` tables for `:J` command completion.
For example, `[sql.main]` contributes `main` under `sql`. Scalar settings and
undiscovered configuration sections are omitted; table contents and credentials
are never included. Alias names must be nonempty single tokens. This convention
does not resolve the provider or validate its private configuration. Families
using another configuration layout remain usable through manually written cells.
