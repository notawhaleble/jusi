# SQL/SQLite/VisiData development fixture

This is a test-only 1.0 exact plugin. It proves family alias resolution and a
real SQLite/VisiData terminal client without packaging SQL dependencies into
core Jusi or modifying the external 0.x `jusi-sql` and `jusi-sqlite` projects.

It is discovered only when this directory is explicitly added to `PYTHONPATH`.

The automated Neovim scenario creates its own database and config. For a manual
run, create equivalents outside the repository:

```sh
.venv/bin/python -c 'import sqlite3; c=sqlite3.connect("/tmp/jusi-sqlite.db"); c.execute("create table if not exists numbers(value integer)"); c.execute("delete from numbers"); c.execute("insert into numbers values (2)"); c.commit()'
printf '%s\n' '[sql.main]' 'provider = "sqlite"' 'path = "/tmp/jusi-sqlite.db"' > /tmp/jusi-sqlite.toml
printf '%s\n' '╭──' '%%sql main' 'select value from numbers' '╰──' > /tmp/jusi-sqlite.vipynb
```

Then launch a clean Neovim from the Jusi repository:

```sh
PYTHONPATH="$PWD/tests/fixtures/sqlite_plugin${PYTHONPATH:+:$PYTHONPATH}" \
  nvim --clean \
  --cmd "set runtimepath^=$PWD" \
  --cmd "lua require('jusi').setup({service_command={'$PWD/.venv/bin/jusi', 'serve', '--config', '/tmp/jusi-sqlite.toml'}})" \
  /tmp/jusi-sqlite.vipynb
```

Run `:JusiServiceStart`, `:JusiStartKernel`, and `:JusiExecute`. VisiData should
open in a native terminal with a `value` column containing `2`. Return to the
notebook, run `:JusiCloseClient`, confirm the kernel remains on, then run
`:JusiServiceStop`.
