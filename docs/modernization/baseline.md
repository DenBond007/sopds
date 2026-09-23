# M00: исходное состояние SOPDS (23.09.2026)

Обновление по решению пользователя: legacy-запуск и исходные тесты не выполняются;
результат тестов до модернизации неизвестен. M00B продолжает работу сразу на
Python 3.13 / Django 5.2. В записи ниже Django 2.2 означает только версию из
`Pipfile.lock`, а не установленный Django: проверка Python 3.13 показала отсутствие
пакета `django`.

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

Прежний путь продолжения через Python 3.7 ниже сохранён только для истории M00 и не
является действующим следующим шагом.

## Исторические команды legacy-продолжения (не выполнять в текущем плане)

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

## M00B: первый запуск на Python 3.13 / Django 5.2

`pyproject.toml` и `uv.lock` задают отдельное окружение Python 3.13.15. Версии из lock:
Django 5.2.17, APScheduler 3.11.3, django-picklefield 3.4.0, lxml 5.4.0,
Pillow 11.3.0, psycopg2-binary 2.9.13; транзитивные версии также зафиксированы.
Версия Django 5.2.17 выбрана в пределах LTS; 5.2.18 на 23.09.2026 ещё не выпущена.
`python-telegram-bot` пока не входит в основное окружение: старый код команды использует
API ветки 13, а её совместимость с Python 3.13 не проверена. Команду телебота не запускали;
её адаптация остаётся за пределами M00B. Старые `requirements.txt` и `Pipfile.lock`
не применялись к Python 3.13; их дальнейший статус решается в M02.

Команды и результат:

| Команда | Exit code | Результат |
|---|---:|---|
| `UV_CACHE_DIR="$PWD/.local/uv-cache" uv lock --python python3.13` | 0 | Разрешено 11 записей lock, включая проект. |
| `UV_CACHE_DIR="$PWD/.local/uv-cache" uv sync --python python3.13 --frozen` | 0 | Создана `.venv`, установлено 9 пакетов. |
| `.venv/bin/python manage.py check` | 0 | После адаптации Django API; 0 замечаний после установки `DEFAULT_AUTO_FIELD=AutoField`. |
| `.venv/bin/python manage.py showmigrations --plan` | 0 | Исторические `database.0001/0002` и `opds_catalog.0001` загружены. |
| `.venv/bin/python manage.py makemigrations --check --dry-run` | 0 | `No changes detected`. |
| `.venv/bin/python manage.py migrate --noinput` на новой `sopds_m00b_test` | 0 | Применена 21 миграция. |
| Повторный `migrate --noinput` и проверка пробной записи Constance | 0 | `M00B_PROBE` сохранился с ID 1; SQL-тип `constance_config.id` — `integer`. |
| `.venv/bin/python manage.py test --noinput` (первый прогон) | 1 | 40 тестов; 20 ошибок из-за удалённых `assertEquals`/`assertNotEquals`, 4 падения по языку книги. |
| `.venv/bin/python manage.py test --noinput` (после замены только имён assert API) | 1 | 40 тестов; 4 падения, 0 ошибок загрузки. |

Для каждой команды Django загружался `.local/dev.env` без вывода содержимого и
проверялись `DB_NAME=sopds_dev`, `DB_USER=sopds_dev`, `DB_HOST=127.0.0.1`,
`DB_PORT=5433`. Для миграций затем явно задавался `DB_NAME=sopds_m00b_test`:
БД была создана только после подтверждения отсутствия. Django-тесты создавали и
удаляли `test_sopds_m00b_test`; существующая `sopds_dev` не очищалась.

Оставшиеся четыре падения: `test_processfile_epub` ожидал `book.lang == 'ru'`,
получил `Русский`; три варианта FB2 ожидали `en`, получили `Английский`. В текущем
`opds_catalog/sopdscan.py` словарь языка преобразует код в полное имя. Без исходных
тестов невозможно классифицировать эти падения как прежние или внесённые при переносе.
Ожидаемые значения и код сканера в M00B не менялись. Полные логи хранятся только в
`.local/logs/m00b-*.log`.

После M00B воспроизводимые команды окружения: `uv sync --python python3.13 --frozen`,
затем `source .local/dev.env` с проверкой имени/адреса/порта БД и `.venv/bin/python
manage.py check`. Для тестов использовать только одноразовую test-БД через
`DB_NAME=sopds_m00b_test .venv/bin/python manage.py test --noinput`; запускать после
проверки, что `test_sopds_m00b_test` не существует. Миграция предоставленной копии
прежней рабочей БД **не проверена**.

## M01.1: защитные тесты данных (в работе M01)

Новые тесты используют только Django `test_sopds_m00b_test` и копию тестовой книги во
временной папке. До исправления `manage.py test
opds_catalog.tests.test_scan_safety --noinput` завершился с кодом 1: из 2 тестов
1 упал, потому что отсутствующий корень не вызывал ошибку и сканер дошёл до
физического удаления. После раннего `os.scandir` и `os.walk(..., onerror=...)`
та же команда завершилась с кодом 0: 2/2 теста прошли; повторный scan сохранил
ID книги, ID каталога и связь с полкой. Полные логи: `.local/logs/m01-safety-before.log`
и `.local/logs/m01-safety-after.log`. Реальная миграция БД остаётся **не проверена**.

## M01.2: подтверждённые риски

- При успешном обходе сканер игнорировал `SOPDS_DELETE_LOGICAL` и физически удалял
  отсутствующие книги, включая связанные полки. Два новых теста воспроизвели потерю
  (`4 теста, 2 ошибки`, exit 1); после восстановления режима логического удаления и
  сохранения книг с полками даже при физическом режиме `4/4`, exit 0. Возврат файла
  восстановил `avail=2` с тем же ID и полкой. Логи `m01-delete-before.log`,
  `m01-delete-after.log`, `m01-delete-restore.log` в `.local/logs/`.
- В OPDS и web префиксы из маршрута/GET подставляются в шесть raw SQL-запросов.
  Тест `test_sql_safety` с `A'B` до исправления дал `ProgrammingError` из PostgreSQL
  (`2 теста, 1 ошибка`, exit 1; `.local/logs/m01-sql-before.log`). Обычный префикс
  проходит. Это подтверждает отсутствие параметризации; возможность иных SQL-атак
  не тестировалась на данных вне одноразовой test-БД.
- `opds_catalog/dl.py` вызывает `Popen(..., shell=True)` в двух путях конвертации,
  собирая командную строку из имени конвертера и файла. Настройки содержат публичный
  резервный `SECRET_KEY`, `DEBUG=True`, `ALLOWED_HOSTS=['*']`. Эти факты установлены
  по коду; команды конвертера и production-настройки не запускались.

M01.3a: шесть OPDS/web запросов переведены на параметры Django `raw(sql, params)`
через общий helper с фиксированными именами моделей/полей. После исправления
`test_sql_safety` прошёл (4/4, exit 0): обычный префикс, кавычка во всех трёх OPDS
группах и три реальные web-страницы. Для последней проверки семь шаблонов переведены
с удалённого `{% load staticfiles %}` на `{% load static %}`. Логи:
`.local/logs/m01-sql-before.log`, `m01-sql-after.log`, `m01-sql-all.log`,
`m01-sql-web-2.log`.
