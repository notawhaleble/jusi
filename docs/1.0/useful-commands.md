# Полезные команды

Эти необязательные вспомогательные команды размещаются в `init.lua`.
Они используют Lua-модули Jusi; держите их рядом с соответствующей
версией установленного фронтенда.

## JShell: оболочка в текущем каталоге

Эта команда открывает или повторно использует ячейку оболочки в загруженном
блокноте, используя каталог текущего файла, текущий каталог netrw или рабочий
каталог Neovim для безымянного буфера. Без диапазона она отправляет `pwd`
через контекстную отправку Jusi. С визуальным выделением или диапазоном строк
она отправляет выделенный текст как тело оболочки, сохраняя историю запросов
ячейки.

Нужен совместимый с Jusi 1.0 плагин `jusi-shell`,
установленный в окружении сервиса и ядра целевого узла, и запущенный блокнот,
в обнаруженной палитре которого есть `shell`. Оболочка работает в целевом
окружении ядра; для Docker или другого удалённого окружения выбранный каталог
должен существовать там. При использовании этой команды согласуйте пути
и точки монтирования.

Вставьте следующее в `init.lua`:

```lua
local function jusi_shell_directory()
  local directory
  if vim.bo.filetype == 'netrw' then
    directory = vim.b.netrw_curdir or vim.t.netrw_curdir or vim.g.netrw_curdir
  end
  if not directory or directory == '' then
    local path = vim.api.nvim_buf_get_name(0)
    directory = path ~= '' and vim.fn.fnamemodify(path, ':h') or vim.fn.getcwd()
  end
  directory = vim.fn.fnamemodify(directory, ':p')
  return directory == '/' and directory or directory:gsub('/$', '')
end

local function shell_argument(value)
  if value:match('^[%w_@%%+=:,./~-]+$') then
    return value
  end
  return vim.fn.shellescape(value)
end

local function jusi_shell_error(message)
  vim.notify(tostring(message), vim.log.levels.ERROR, { title = 'JShell' })
end

local function set_jusi_magic_body(cell, body)
  if type(cell) ~= 'table' or vim.tbl_isempty(cell) then
    return nil, 'Jusi не удалось определить ячейку оболочки'
  end

  local bufnr = vim.api.nvim_get_current_buf()
  local editor = require('jusi.editing').attach(bufnr)
  local snapshot = editor.model:cell_snapshot(cell)
  if not snapshot then
    return nil, 'Ячейка оболочки Jusi больше не существует'
  end

  -- body_start_row — строка заголовка магической команды; сохраните её
  -- и замените всё до body_end_row, не включая историю и закрывающую границу.
  vim.api.nvim_buf_set_lines(bufnr, snapshot.body_start_row + 1, snapshot.body_end_row, false, { body })
  editor.model:flush()
  snapshot = editor.model:cell_snapshot(cell)
  if not snapshot then
    return nil, 'Не удалось обновить ячейку оболочки Jusi после замены её тела'
  end
  vim.api.nvim_win_set_cursor(0, { snapshot.body_start_row + 2, 0 })
  return snapshot
end

vim.api.nvim_create_user_command('JShell', function(opts)
  local notebook = vim.trim(opts.args)
  local command = {
    args = '',
    fargs = { notebook, 'shell', '--cwd', shell_argument(jusi_shell_directory()) },
    bang = opts.range > 0,
    range = opts.range,
    line1 = opts.line1,
    line2 = opts.line2,
  }
  local ok, cell = pcall(require('jusi.palette').command, command)
  if not ok then
    jusi_shell_error(cell)
    return
  end
  if opts.range > 0 then
    return
  end

  vim.cmd.stopinsert()
  local snapshot, err = set_jusi_magic_body(cell, 'pwd')
  if not snapshot then
    jusi_shell_error(err)
    return
  end
  require('jusi').submit(vim.api.nvim_get_current_buf(), snapshot.body_start_row + 1)
end, {
  nargs = 1,
  range = true,
  complete = function(arglead)
    local command_line = 'J ' .. arglead
    local ok, aliases = pcall(require('jusi.palette').complete, arglead, command_line, #command_line)
    return ok and aliases or {}
  end,
  desc = 'Открыть оболочку Jusi в каталоге текущего файла или netrw',
  force = true,
})
```

Для загруженного блокнота `work.vipynb` выполните:

```vim
:JShell work
```

По Tab доступно автодополнение имён блокнотов. Выделите команды оболочки
в другом буфере и используйте `:'<,'>JShell work` или отправьте диапазон строк
через `:1,3JShell work`. Блокнот открывается в текущей вкладке; выполнение
показывает вывод оболочки, сохраняя фокус в блокноте. Используйте сочетание
переключения фокуса Jusi — Ctrl-\ дважды — для перехода в терминал.
Базовые команды `:J` / `:J!` описаны
в [руководстве по палитре](usage.md#palette-and-focus).

<a id="clean-up-the-current-notebook-view"></a>

## Очистка текущих окон блокнота

После временной работы через JShell или другую команду `:J` нажмите `\Q`
в блокноте или выводе в обычном режиме, включая режим ячеек блокнота.
Встроенная команда `:JusiCloseTab` закрывает видимый в текущей вкладке вывод
этого блокнота и удаляет его дополнительные окна. Она сохраняет последнее
окно блокнота среди всех вкладок и оставляет ядро работающим.
Дополнительная настройка вспомогательных команд не требуется.

Клиенты, видимые в другой вкладке, остаются доступны там; закрываются только
их окна текущей вкладки. Скрытые клиенты, посторонние окна проекта и экспорты
приложений сохраняются. При ошибке очистки клиента окно блокнота остаётся
для повторной попытки. Существующие пользовательские привязки `\Q`
имеют приоритет.
