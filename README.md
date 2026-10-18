# SOPDS

Этот Django-проект модернизируется для Python 3.13, Django 5.2 LTS и
PostgreSQL 17. Текущее состояние, результаты проверок и ограничения записаны в
[плане](docs/modernization/PLAN.md), [состоянии](docs/modernization/STATE.md)
и [журнале проверок](docs/modernization/baseline.md).


## Личная библиотека и обслуживание

Поиск поддерживает совместные фильтры. После входа доступны списки «Хочу прочитать»,
«Читаю», «Прочитано» и FB2-читалка с сохранением позиции между устройствами.
Страница `/web/operations/` показывает состояние служб и резервирования только staff.
[Руководство M08](docs/modernization/m08-library-operations.md) описывает новую
миграцию, проверенные backup/restore, ежедневное расписание и измерения производительности.
[Сборка CSS и браузерные тесты](assets/sopds-sass/README.md) воспроизводимы через npm-lock.

## Целевое размещение

Выбран Docker: [Dockerfile](Dockerfile) и [Compose](infra/docker/compose.yaml)
разделяют web, scanner, annotations, PostgreSQL и Caddy.
[Инструкция подготовки и проверки](docs/modernization/docker.md) использует
отдельную искусственную библиотеку и HTTPS-порт 9444. Сборка и контейнерный smoke на linux/amd64 проверены;
временные нативные службы на 8443 сейчас отсутствуют; их файлы сохранены. Docker-контур M08 работает на 9444 с искусственными данными, политикой restart и ежедневным проверенным резервированием.
Шаблоны systemd сохранены как дополнительный вариант нативного запуска.
Для TrueNAS SCALE 24.10+ подготовлены [приватные GHCR-образы и инструкция](docs/modernization/truenas.md):
сборка выбранного commit/тега в приватном `sopds-images` после CI, небольшой комплект с digest-ссылками, генерация приватного YAML,
отдельная настройка новой БД и проверяемое резервирование. Автономный архив образов также поддерживается.
Установка на NAS выполняется отдельно.

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

Для фонового извлечения INPX-аннотаций запустите отдельный
`manage.py sopds_annotation_worker` после миграций и настройки библиотеки.
Порядок запуска, остановки и восстановления приведён в
[инструкции по аннотациям](docs/modernization/annotations.md).

Порядок снимка БД/библиотеки, пробного восстановления и отката описан в
[операторской инструкции](docs/modernization/operations.md).

[Результат проверки кандидата](docs/modernization/candidate.md) перечисляет
подтверждённые проверки и условия перед переносом реальных данных.

Проверенная команда тестов и условие отсутствия одноразовой test-БД приведены в
[журнале проверок](docs/modernization/baseline.md). GitHub Actions M08: [203/203 теста с Chromium](https://github.com/DenBond007/sopds/actions/runs/39117577380) за 82.696 с. Локально: 200 общих тестов и три дополнительных теста расписания и backup-lock прошли. Прежний M07 внутри образа: 182/182. Исходный статус тестов до модернизации неизвестен.

Предыдущая инструкция для старых версий Python, Django и других БД сохранена
[как исторический документ](docs/legacy/README.md). Её команды установки и
очистки каталога не применимы к текущему окружению. Старые Pipenv/requirements,
инструкция Raspberry и Travis-конфигурация также находятся в `docs/legacy/`;
test-only GitHub Actions настроен. CI дополнен проверками CSS, синтаксиса и Chromium. Ветка codex/library-operations опубликована по явному согласию пользователя; удалённые проверки прошли, результат записан в STATE/PLAN.
