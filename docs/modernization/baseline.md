# M00: исходное состояние SOPDS (23.09.2026)

## Git и окружение

- Исходный commit: `ce0ba45003cbff02881dc3cd822c22061210e73e`, ветка `modernization/sopds`.
- Remotes: `origin=https://github.com/DenBond007/sopds.git`, `upstream=https://github.com/ichbinkirgiz/sopds.git` (fetch/push).
- До работ `git status --short --branch` показывал только чужой неотслеживаемый `._sopds-codex-start.zip`; файл не изменялся и не добавлялся в индекс.
- Ubuntu 24.04.5 LTS, x86_64; системный Python 3.12.3, отдельный целевой Python 3.13.15, uv 0.12.18. `psql` и `pg_isready` 16.15; сервер dev-БД 17.11.
- В `Pipfile` и `Pipfile.lock` указан Python 3.7. Lock фиксирует Django 2.2 и 15 других пакетов, в том числе Pillow 6.0.0, lxml 4.3.3, APScheduler 3.6.0 и python-telegram-bot 11.1.0. `requirements.txt` расходится с lock: Django `<2.2`, `python-telegram-bot==13.5`, `tzlocal==2.1`, остальные диапазоны открыты. Встроенная `constance` находится в репозитории. Для PostgreSQL необходим psycopg2, которого нет ни в lock, ни в requirements. Обычный `uv sync` здесь не является способом запуска старого checkout.
- Установлены `build-essential`, `libpq-dev` 16.15, `zlib1g-dev`, `libffi-dev`, `libssl-dev`, `postgresql-client` 16. Не установлены `libxml2-dev`, `libxslt1-dev`, `libjpeg-dev`; они могут понадобиться, если старые lxml/Pillow придётся собирать из исходников. Системные пакеты устанавливает человек.

## Проверенная локальная подготовка

- `python3 scripts/dev/prepare-local.py` завершился с кодом 0: существующие локальные файлы оставлены без изменений. Их содержимое не выводилось; `.local/postgres.env` не читался.
- `SOPDS_DEV_LIBRARY` в `.local/dev.env` — подсказка для будущего dev-запуска. Старый код читает `config.SOPDS_ROOT_LIB` из БД Constance; значение по умолчанию `books/`. Для сканера его нужно явно установить в `/home/denbond/Projects/sopds/.local/library`. Папка существует, не является ссылкой, на верхнем уровне пуста. Шесть файлов данных исходных тестов отслеживаются Git в `opds_catalog/tests/data/`; их не переносили в рабочую библиотеку.
- После проверки `DB_NAME=sopds_dev`, `DB_USER=sopds_dev`, `DB_HOST=127.0.0.1`, `DB_PORT=5433` read-only `pg_isready` сообщил `accepting connections`. `psql` подтвердил `current_database=sopds_dev`, `current_user=sopds_dev`, PostgreSQL 17.11, `CREATEDB=true`, 0 таблиц в `public` и отсутствие `test_sopds_dev`. Docker socket не использовался; контейнер не запускался агентом.
- Наличие копии прежней рабочей БД не подтверждено. Миграция её данных не проверена.

## Исходные тесты и блокер

Исходная команда проекта по `.travis.yml`: `python manage.py test`; в данном checkout она должна выполняться из отдельного окружения Python 3.7 с загруженным `.local/dev.env`, через отдельную dev-БД. Исходные тесты **не запускались**: совместимый интерпретатор отсутствует, а ставить старые зависимости в Python 3.13 нельзя. Следовательно, числа пройденных/упавших тестов нет; этот блокер не является дефектом приложения.

Проверки интерпретатора: `uv python list --only-installed` нашёл только 3.12 и 3.13; `python3.7`, `pyenv`, `pipenv` и `.venv-legacy` отсутствуют. Одна попытка `UV_PYTHON_INSTALL_DIR="$PWD/.local/python" UV_CACHE_DIR="$PWD/.local/uv-cache" uv python install 3.7` завершилась с кодом 2: `No download found for request: cpython-3.7-linux-x86_64-gnu`. Повторять ту же команду без новой диагностики бессмысленно.

Минимальное действие человека: предоставить путь к работающему Python 3.7 в этой VM либо разрешить его отдельную установку/сборку без замены `/usr/bin/python3`; для сборки при необходимости установить системные build-зависимости. Затем можно создать `.venv-legacy`, отдельно разрешить установку пакетов для этого окружения и проверить расхождение lock/requirements и отсутствующий драйвер psycopg2 до запуска тестов. Версии и способ установки этих пакетов пока не проверены, поэтому команда установки зависимостей не выдаётся за воспроизводимую.

## Команды для продолжения после устранения блокера

Эти команды **не выполнялись** и не подтверждают готовый запуск. Они намеренно проверяют назначение перед любым обращением к БД. Запускать их только из корня этого checkout после создания `.venv-legacy` на Python 3.7 и установки проверенного набора старых зависимостей.

```bash
set -e
python3 scripts/dev/prepare-local.py
set -a
. .local/dev.env
set +a
test "$DB_NAME" = sopds_dev && test "$DB_USER" = sopds_dev && \
  test "$DB_HOST" = 127.0.0.1 && test "$DB_PORT" = 5433
test "$(realpath .local/library)" = "$PWD/.local/library" && test ! -L .local/library
test -x .venv-legacy/bin/python
PGPASSWORD="$DB_PASS" psql -X -h "$DB_HOST" -p "$DB_PORT" \
  -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -Atqc 'select current_database(), current_user'
.venv-legacy/bin/python manage.py check
.venv-legacy/bin/python manage.py migrate --noinput
.venv-legacy/bin/python manage.py sopds_util setconf SOPDS_ROOT_LIB "$PWD/.local/library"
.venv-legacy/bin/python manage.py test --noinput
```

Для безопасного ручного dev-запуска после успешных проверок: `.venv-legacy/bin/python manage.py runserver 127.0.0.1:8000 --noreload`. Перед **любым** запуском сканера ещё раз проверить `sopds_util getconf SOPDS_ROOT_LIB`: он должен указывать строго на `.local/library`; не запускать `sopds_scanner` с настройкой по умолчанию или на рабочей библиотеке. Логи проверок хранить в `.local/logs/`, фиксируя код выхода и относящиеся к нему ошибки в этом файле.
