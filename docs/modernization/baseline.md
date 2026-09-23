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

M01.3b: до исправления буквальное имя тестового конвертера с `$(...)` расширялось
оболочкой (`1 тест, 1 падение`, exit 1). Оба пути `Popen` переведены на список
аргументов без shell. После исправления тест прямой конвертации и тест HTTP-пути
прошли (`2/2`, exit 0), работая только с временными тестовыми файлами. Логи:
`.local/logs/m01-converter-before.log`, `m01-converter-after.log`,
`m01-converter-both.log`.

M01.3c: `prepare-local.py` дополняет существующий `.local/dev.env` только
отсутствующими настройками приложения, включая случайный `SOPDS_SECRET_KEY`, без
вывода значения и без изменения учётных данных БД. Django больше не содержит
публичный резервный ключ; без ключа `manage.py check` завершился с кодом 1,
с локальным ключом — с кодом 0. DEBUG по умолчанию выключен, hosts по умолчанию
ограничены localhost. Два subprocess-теста настроек прошли (exit 0). Логи:
`.local/logs/m01-settings-check.log`, `m01-settings-missing-secret.log`,
`m01-settings-tests.log`. Production-настройки и секреты не проверялись.

M01.3d: повторный `python3 scripts/dev/prepare-local.py` завершился с кодом 0;
SHA-256 `.local/dev.env` до и после совпал (содержимое не выводилось). Перед
общим прогоном `test_sopds_m00b_test` отсутствовала. Команда
`DB_NAME=sopds_m00b_test .venv/bin/python manage.py test --noinput` выполнила
53 теста за 5.961 с, exit 1: те же 4 падения по `book.lang` (`Русский`/`ru`,
`Английский`/`en`), 0 ошибок, Django check без замечаний. После прогона
`test_sopds_m00b_test` отсутствует. Полный лог: `.local/logs/m01-full.log`.
Тесты сохранности ID и полок, отказа при недоступном корне и сбое обхода,
SQL-параметризации, конвертера и настроек прошли в общем наборе. Поскольку
legacy-тесты не запускались, исходный статус четырёх падений остаётся неизвестным.
Миграция копии прежней рабочей БД также не проверена.

## M02.1: источники зависимостей и ссылки

Для Python 3.13 активны `pyproject.toml` и `uv.lock`: Django 5.2.17,
APScheduler 3.11, django-picklefield 3.3–3.4, lxml 5, Pillow 11 и
psycopg2-binary 2.9. Старые `requirements.txt`, `Pipfile` и `Pipfile.lock`
расходятся с ними: требования Python 3.7/Django до 2.2, старые версии библиотек,
Telegram в обоих списках и отсутствие драйвера PostgreSQL. Lock Pipenv содержит
16 пакетов; это историческая запись, не проверенное окружение. Прямые ссылки на
`requirements.txt` остаются в README, `raspberry.txt` и `.travis.yml`.
Команда `sopds_telebot` импортирует `telegram`, но пакет исключён из основного
окружения M00B до отдельной адаптации. Пакеты в M02.1 не устанавливались.
Следующее изменение — обновить основную инструкцию README под uv и отдельную
dev-БД; затем разобраться с raspberry.txt, Travis и старыми manifests, чтобы не
оставить конкурирующих активных способов установки.

M02.2: исходный README перенесён без изменений в `docs/legacy/README.md`.
Корневой README теперь описывает Python 3.13, uv, отдельную dev-БД PostgreSQL 17,
проверку её имени/адреса и настройку `SOPDS_ROOT_LIB` на `.local/library` до
сканера. Проверка `bash -n` блока команд и ссылок прошла; в основной инструкции
нет установки `requirements.txt` и команды `sopds_util clear`. Команды из README
заново не выполнялись; успешные команды среды и ограничения указаны выше.

M02.3: `.travis.yml`, `raspberry.txt`, `requirements.txt`, `Pipfile` и
`Pipfile.lock` перенесены без изменения байтов в `docs/legacy/`; корневых копий
нет. В действующих файлах больше нет команд установки старого стека.
`UV_CACHE_DIR="$PWD/.local/uv-cache" uv lock --check --offline` завершился с
кодом 0 (11 пакетов). Старый Travis job отключён переносом конфигурации;
автоматический CI для Python 3.13/PostgreSQL 17 пока не настроен. Исторические
материалы не являются инструкцией к запуску.

M02.4: с загруженным `.local/dev.env` и `DB_NAME=sopds_m00b_test` команды
`manage.py check`, `makemigrations --check --dry-run` и `showmigrations --plan`
завершились с кодом 0: замечаний нет, изменений схемы нет, исторические миграции
Constance и каталога отмечены применёнными. Это проверка одноразовой БД, не
миграция копии прежней рабочей БД. `manage.py help sopds_telebot` завершился с
кодом 1 (`ModuleNotFoundError: No module named 'telegram'`); бот не запускался.
Старый Redis backend содержит импорт удалённого `django.utils.six`, но backend
неактивен и Redis не входит в целевой стек. Логи: `.local/logs/m02-check.log`,
`m02-migrations-check.log`, `m02-migrations-plan.log`, `m02-telebot-help.log`.

M02.5: [PyPI python-telegram-bot 22.8](https://pypi.org/project/python-telegram-bot/22.8/)
указывает Python >=3.10 и поддержку Python 3.13. [Официальный переходный
документ v20](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Transition-guide-to-Version-20.0)
описывает переход на asyncio, `Application` и `filters`: старые `Updater`,
`Filters` и синхронные callback нельзя оставить как есть. [Документация Django
5.2](https://docs.djangoproject.com/en/5.2/topics/async/) требует выносить
синхронный ORM из async-контекста через `sync_to_async`. В текущем боте шесть
callback и два декоратора, несколько вызовов Bot API и синхронный доступ к ORM;
следовательно, нужна адаптация всей команды и адресные тесты с подменой Bot API.
Живой токен и Telegram-сеть для тестирования не используются. Библиотека будет
добавлена в основной стек, как и в исторических requirements, чтобы команда
загружалась при обычном запуске Django.

M02.6a: python-telegram-bot 22.8 добавлен в `pyproject.toml`, `uv lock` разрешил
19 пакетов, `uv sync --python python3.13 --frozen` установил 8 новых пакетов
только в `.venv` (обе команды exit 0). `uv lock --check --offline`, импорт с
версией 22.8 и Django check прошли. `manage.py help sopds_telebot` теперь
завершается с кодом 1 на `ImportError: cannot import name 'Filters'` — ожидаемый
разрыв API v13, подзадача M02.6b. Бот не запускался. Логи:
`.local/logs/m02-telegram-lock.log`, `m02-telegram-sync.log`,
`m02-telegram-help-after-dep.log`, `m02-telegram-check.log`.

M02.6b: команда переведена на `ApplicationBuilder`, `filters` и async callbacks;
синхронные обращения к Constance/ORM и подготовка файла выполняются через
`sync_to_async`, кеш поиска содержит вычисленные списки, файл закрывается после
отправки. `manage.py help sopds_telebot` прошёл без токена и сети. Четыре
адресных теста на одноразовой БД и временном файле прошли (exit 0): поиск,
сохранение ID в кнопке загрузки, закрытие файла и проверка доступа. Первый
прогон тестов выявил несовместимость `close_old_connections` с транзакцией
`TestCase`; изолированные Telegram-тесты переведены на `TransactionTestCase`
и прошли повторно. Общий набор: 57 тестов, exit 1, 4 прежних падения по
`Book.lang`, 0 ошибок, замечаний Django нет. После тестов test-БД отсутствует.
Живой Telegram-бот и доставка сообщений по сети не проверялись. Логи:
`.local/logs/m02-telegram-help-ported.log`, `m02-telegram-tests-final.log`,
`m02-telegram-full.log`.

M02.7: четыре падения касались двух ожиданий теста сканера (`en`/`ru`).
Неизменённый код `opdsScanner` переводит эти коды в `Английский`/`Русский`
встроенным словарём при отсутствии `Languages.txt`, а web-шаблон отображает
сохранённый `Book.lang`. Ожидания уточнены для фактической отображаемой
семантики; дополнительный тест проверяет пользовательскую замену имени через
временный `Languages.txt` и копию искусственной книги. Целевой набор
`opds_catalog.tests.test_scan`: 8/8, exit 0; лог `.local/logs/m02-language-tests.log`.
Это не доказывает, что четыре теста проходили в старом окружении: исходный запуск
пропущен по решению пользователя.

`git blame` относит преобразование языка в `sopdscan.py` к коммиту
`3131f60e` от 2023-05-20; это не изменение модернизации. Результат исходных
legacy-тестов всё равно неизвестен, поскольку они не запускались.

M02.8: общий `DB_NAME=sopds_m00b_test .venv/bin/python manage.py test --noinput`
выполнил 58 тестов за 6.175 с, exit 0, замечаний Django нет.
`makemigrations --check --dry-run`, повторный `migrate --noinput` и
`showmigrations --plan` завершились с кодом 0: изменений моделей и ожидающих
миграций нет. В отдельной `sopds_m00b_test` запись `M00B_PROBE` осталась одна
до и после повторной миграции, тип `constance_config.id` — `integer`; значение
не выводилось. Одноразовая `test_sopds_m00b_test` после тестов отсутствует.
Логи: `.local/logs/m02-full.log`, `m02-final-makemigrations.log`,
`m02-final-migrate.log`, `m02-final-plan.log`. Копия прежней рабочей БД не
предоставлена, поэтому миграция её данных не проверена. Живой Telegram-бот и
автоматический CI для нового стека также не проверены.

## M03.1: конкурирующие запуски

`Command.scan()` использовал только `scan_is_active` экземпляра, затем длинную
транзакцию сканирования и обновление счётчиков вне неё. Две разные команды с
отдельными соединениями одновременно вошли в подменённый scan на временном
корне: тест ожидал 1 вход, получил 2 (exit 1). Первый прогон оставил соединения
потоков открытыми и Django не смог удалить `test_sopds_m00b_test`; после
проверки нуля активных сессий была удалена только эта одноразовая БД. В тест
добавлено закрытие соединений потоков; повтор сохранил единственное ожидаемое
падение `2 != 1`, teardown завершился. Логи: `.local/logs/m03-concurrency-before.log`,
`m03-concurrency-before-2.log`. Рабочая библиотека и dev-БД не сканировались.
Блокировка должна охватывать scan и обновление счётчиков, сохраняя возможность
разделить длинную транзакцию позже; выбран session advisory lock PostgreSQL.

M03.2: [PostgreSQL 17](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)
удерживает session advisory lock до явного освобождения или завершения сессии,
в том числе после rollback. `Command.scan()` теперь получает lock до обхода,
удерживает его при обновлении счётчиков, пропускает второй запуск без ожидания
и освобождает lock вместе со сбросом `scan_is_active` в `finally`.
Первый адресный прогон обнаружил, что `close_old_connections()` закрывает
соединение внешней транзакции `TestCase`; заменено на закрытие только
неисправного соединения. Повторный прогон: 7/7 тестов сохранности и конкуренции,
exit 0. Общий набор: 60/60, exit 0, без замечаний Django. После него одноразовая
test-БД отсутствует. Логи: `.local/logs/m03-lock-tests-1.log`,
`m03-lock-tests-2.log`, `m03-lock-tests-3.log`, `m03-lock-full.log`.

M03.3: на копии отслеживаемой тестовой FB2 в `TemporaryDirectory` заменён
заголовок на строку той же байтовой длины и увеличен mtime. Повторный scan
сохранил прежний заголовок (`1 тест, 1 падение`, exit 1), хотя ID и полка
должны оставаться прежними. Лог: `.local/logs/m03-change-before.log`.
`Book` хранит filesize, но не время изменения; `findbook` при совпадении
имени/пути всегда пропускает разбор. Для обычного файла выбран nullable
`source_mtime_ns` + существующий filesize. При изменении метаданные, авторы,
жанры и серии обновляются в прежней строке только после успешного разбора;
ID, полка и registerdate сохраняются. Архивы/INPX требуют отдельного решения,
поскольку сейчас их пропуск основан только на размере каталога.

M03.4a: `opds_catalog.0002_book_source_mtime_ns` добавляет только nullable
BigIntegerField к `Book`; историческую `0001_initial` не меняли. Тест через
`MigrationExecutor` создал книгу и полку на старой схеме, применил 0002 и
подтвердил прежние ID/связь и `source_mtime_ns=NULL` (1/1, exit 0).
`makemigrations --check --dry-run` завершился с кодом 0, изменений сверх 0002
нет. Логи: `.local/logs/m03-mtime-makemigrations.log`,
`m03-mtime-migration-test.log`, `m03-mtime-schema-check.log`. Реальная прежняя
БД не предоставлена, поэтому её миграция не проверена.

M03.4b: обычный файл сравнивается по размеру и `st_mtime_ns`; при изменении
книга после разбора обновляется по прежнему ID вместе с авторами, жанрами и
сериями. Запись метаданных и связей атомарна (savepoint внутри транзакции
команды): ошибка записи откатывает её без потери прежней книги и полки.
Тест на временной копии FB2
изменил заголовок, автора и жанр при том же размере; ID, registerdate и полка
сохранились. Отдельно проверены повреждённая замена и искусственная ошибка
записи жанра; старая запись и связи сохранились. Адресные 3/3 теста, exit 0;
общий набор 64/64, exit 0, замечаний Django нет. После тестов одноразовая
`test_sopds_m00b_test` отсутствует. Логи: `.local/logs/m03-change-targeted-2.log`,
`m03-change-general.log`. Архивы/INPX не охвачены этим изменением. Копии
прежней рабочей БД нет.

M03.5: на временных ZIP и INPX с искусственными книгами заменён заголовок при
прежнем размере архива. Повторный scan сохранил старые заголовки в обоих
случаях: 2 теста, 2 ожидаемых падения, exit 1; одноразовая test-БД удалена.
В обоих внешних проверках пропуска сравнивается только `Catalog.cat_size`;
для INPX дополнительно есть пропуск вложенного INP по размеру и callback,
который не обновляет уже существующую книгу. Лог:
`.local/logs/m03-archive-before.log`.

M03.6a: миграция `opds_catalog.0003_catalog_source_mtime_ns` добавляет только
nullable BigIntegerField к `Catalog`. Тест цепочки 0001→0003 на одноразовой
БД подтвердил сохранность ID каталога/книги/полки и NULL у старых маркеров
времени книги и каталога (1/1, exit 0). `makemigrations --check --dry-run`
прошёл, test-БД удалена. Логи: `.local/logs/m03-catalog-makemigrations.log`,
`m03-catalog-migration-test.log`. Копии прежней рабочей БД нет.
