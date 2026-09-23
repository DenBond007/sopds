# SOPDS

Этот Django-проект модернизируется для Python 3.13, Django 5.2 LTS и
PostgreSQL 17. Текущее состояние, результаты проверок и ограничения записаны в
[плане](docs/modernization/PLAN.md), [состоянии](docs/modernization/STATE.md)
и [журнале проверок](docs/modernization/baseline.md).

## Локальная разработка

Приложение запускается нативно в Ubuntu 24.04. PostgreSQL 17 для разработки
предоставляется отдельно; команды ниже выполняются только с отдельной dev-БД и
тестовой библиотекой. Не направляйте их на рабочую БД или библиотеку.

Из корня checkout:

```bash
set -e
python3 scripts/dev/prepare-local.py
UV_CACHE_DIR="$PWD/.local/uv-cache" uv sync --python python3.13 --frozen
set -a
. .local/dev.env
set +a
test "$DB_NAME" = sopds_dev && test "$DB_USER" = sopds_dev && \
  test "$DB_HOST" = 127.0.0.1 && test "$DB_PORT" = 5433
test "$(realpath .local/library)" = "$PWD/.local/library" && test ! -L .local/library
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py sopds_util setconf SOPDS_ROOT_LIB "$PWD/.local/library"
.venv/bin/python manage.py sopds_util getconf SOPDS_ROOT_LIB
.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload
```

Перед запуском сканера проверьте, что `SOPDS_ROOT_LIB` указывает именно на
`.local/library`. `SOPDS_DEV_LIBRARY` в `.local/dev.env` сам по себе не меняет
настройку старого сканера. Содержимое файлов `.local/*.env` не выводите и не
добавляйте в Git.

Проверенная команда тестов и условие отсутствия одноразовой test-БД приведены в
[журнале проверок](docs/modernization/baseline.md). Сейчас общий набор выполняет
53 теста; 4 теста языка книг падают, их исходный статус до модернизации неизвестен.

Предыдущая инструкция для старых версий Python, Django и других БД сохранена
[как исторический документ](docs/legacy/README.md). Её команды установки и
очистки каталога не применимы к текущему окружению.
