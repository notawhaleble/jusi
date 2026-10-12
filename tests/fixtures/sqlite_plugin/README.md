# Фикстура разработки SQL/SQLite/VisiData

Это конкретный плагин 1.0 только для тестов. Он проверяет разрешение псевдонимов
семейства и настоящий терминальный клиент SQLite/VisiData без включения
зависимостей SQL в ядро Jusi или изменения внешних проектов 0.x `jusi-sql`
и `jusi-sqlite`.

Он обнаруживается только при явном добавлении этого каталога в `PYTHONPATH`.

Фикстура намеренно разделяет четыре роли процессов по отдельным модулям импорта:

- `jusi_sqlite_catalog`: метаданные обнаружения, содержащие только данные.
- `jusi_sqlite_kernel`: адаптер IPython и передача управления через `%%sql`.
- `jusi_sqlite_worker`: изолированный рабочий процесс клиента и закрытые данные запуска.
- `jusi_sqlite_application`: запрос SQLite и терминальное приложение VisiData.

Не объединяйте эти роли обратно в один модуль импорта. В частности,
рабочий процесс и терминальное приложение не должны импортировать IPython;
запуск нового клиента виден пользователю.

Автоматизированный сценарий Neovim создаёт собственные базу и конфигурацию.
Для ручного запуска создайте аналогичные файлы вне репозитория:

```sh
.venv/bin/python -c 'import sqlite3; c=sqlite3.connect("/tmp/jusi-sqlite.db"); c.execute("create table if not exists numbers(value integer)"); c.execute("delete from numbers"); c.execute("insert into numbers values (2)"); c.commit()'
printf '%s\n' '[sql.main]' 'provider = "sqlite"' 'path = "/tmp/jusi-sqlite.db"' > /tmp/jusi-sqlite.toml
printf '%s\n' '╭──' '%%sql main' 'select value from numbers' '╰──' > /tmp/jusi-sqlite.vipynb
```

Затем из репозитория Jusi запустите Neovim без пользовательской конфигурации:

```sh
PYTHONPATH="$PWD/tests/fixtures/sqlite_plugin${PYTHONPATH:+:$PYTHONPATH}" \
  nvim --clean \
  --cmd "set runtimepath^=$PWD" \
  --cmd "lua require('jusi').setup({service_command={'$PWD/.venv/bin/jusi', 'serve', '--config', '/tmp/jusi-sqlite.toml'}})" \
  /tmp/jusi-sqlite.vipynb
```

Выполните `:JusiServiceStart`, `:JusiStartKernel` и `:JusiExecute`.
VisiData должна открыться в стандартном терминале со столбцом `value`,
содержащим `2`. Вернитесь в блокнот, выполните `:JusiClose`, убедитесь,
что ядро остаётся включённым, затем выполните `:JusiServiceStop`.
