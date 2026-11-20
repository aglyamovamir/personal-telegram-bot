# Проверка изменений 21 ноября 2026

## Результат

- Полный набор: **220 тестов успешно**, включая 12 новых проверок Sheets.
- Локальный Python: 3.14.4; gspread: 6.2.1. Синтаксис всех 60 Python-файлов
  отдельно разобран с грамматикой Python 3.11 из конфигурации Amvera.
  Полный запуск на самом Python 3.11 из этой среды не выполнялся.
- `pip check` и `git diff --check` прошли; относительные ссылки документации
  существуют; импорты удалённых клавиатур и форматтера отсутствуют.
- Документы перенесены в `docs/`, их содержимое сохранено с обновлением ссылок
  и пояснениями об исторических отчётах. Корневой пример парсера заменён
  тремя автоматическими проверками, старые клавиатуры не используются.
- `.env`, credentials, SQLite, журналы и резервные копии исключены из Git.
  Проверены 11 вариантов чувствительных путей и известные форматы токенов
  и закрытых ключей в публикуемых текстовых файлах; совпадений нет.

Изменения Sheets описаны в [отдельном документе](google-sheets.md).
Настройки запуска, схема БД и код обработчиков не менялись. Реальные базы,
ключи и `.env` не изменялись; polling и миграции не запускались.

## GitHub и Amvera

Исходная локальная ветка: `chore/project-cleanup`, HEAD `cb0e74c`.
Этот локальный коммит с первой версией исправления Sheets ещё не был на GitHub.
Удалённый `master` на момент проверки: `b47c81f`.

Запись в локальную `.git` запрещена средой помощника: `git add` получил
`Permission denied` при создании `index.lock`. Поэтому локальные изменения
сохранены в рабочей папке, а проверенный результат публикуется отдельной
веткой `review/project-cleanup` через подключение GitHub. Она содержит три
логических коммита от удалённого `master`: исправление тестовой даты,
исправления Sheets, реорганизацию. Первая локальная версия Sheets включена
в итоговое исправление; исходный локальный коммит не переписывался.

`origin` — GitHub `aglyamovamir/personal-telegram-bot`.
`amvera` — SSH-репозиторий `git.mia0.amvera.ru:aglyamovamir/telegram-bot`.
`amvera.yaml` задаёт Python 3.11, `python main.py`, `/data` и singleton.
Публикация в Amvera не выполнялась. SSH-запрос к её репозиторию блокируется
средой, поэтому текущий серверный commit, переменные и логи не подтверждены.
Доступ service account к рабочей таблице также не подтверждён: тесты Sheets
проверяют реальные классы gspread с подставным транспортом без сети.

## Завершение локальных коммитов в обычном PowerShell

Эти команды предназначены для исходной папки проекта и исходной ветки
`chore/project-cleanup`. Ветка `review/project-cleanup` имеет другую историю,
поэтому не нужно пытаться обновить её обычным push из локальной ветки.
Для локальной истории и её публикации используются три отдельные группы ниже.
Если команда завершилась ошибкой, остановитесь перед следующей группой.
Проверьте состав staged diff перед каждым commit; не включайте новые
несвязанные изменения, если они появились после этой проверки.

```powershell
Set-Location -LiteralPath 'C:\Users\Camat\OneDrive\Рабочий стол\Project'
git status --short --branch
git diff --cached --name-status
```

Индекс должен быть пустым. Вначале исправление независимости теста от даты:

```powershell
git add -- tests/test_release_hotfix.py
git diff --cached --check
git diff --cached --stat
git commit -m "Make history ordering test independent of today's date"
```

Затем исправления Sheets поверх существующего локального `cb0e74c`:

```powershell
git add -- google_sheets.py requirements.txt tests/test_google_sheets.py
git diff --cached --check
git diff --cached --stat
git commit -m "Fix numeric and idempotent Google Sheets workout exports"
```

Оставшаяся группа — документация, ignore-правила, удалённые неиспользуемые
клавиатуры, корневой пример и форматтер. `git add -u` индексирует только
изменения уже отслеживаемых файлов; новые файлы добавлены явными путями.

```powershell
git add -u
git add -- README.md docs tests/test_parser.py
git diff --cached --check
git diff --cached --name-status
git commit -m "Organize documentation and remove unused legacy helpers"
git status --short --branch
git push -u origin chore/project-cleanup
```

Публикация явно адресована GitHub и рабочей ветке. Серверный деплой — отдельный
шаг после проверки конфигурации и логов Amvera; команды деплоя здесь нет.
