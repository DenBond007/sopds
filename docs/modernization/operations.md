# Кандидат SOPDS: резервная копия и откат

Эти команды предназначены для отдельной БД разработки и искусственной
библиотеки. Пример использует значения `.local/dev.env`; перед каждой
операцией проверьте назначение. Работа с реальной БД потребует отдельного
плана и предоставленной копии. Scan, web и worker остановите до снимка,
чтобы БД и файлы книги относились к одному состоянию.
Для M06 пользователь выбрал новую установку: проверяется backup/restore
только dev-БД, перенос и сравнение с прежней рабочей БД неприменимы.

## Проверки перед снимком

Нужен клиент `pg_dump`/`pg_restore` версии **17 или новее** для сервера
PostgreSQL 17. Клиент 17.11 установлен человеком и проверен в M06.2.
Перед каждым новым снимком проверьте:

```bash
set -e
set -a
. .local/dev.env
set +a
test "$DB_NAME" = sopds_dev && test "$DB_USER" = sopds_dev && \
  test "$DB_HOST" = 127.0.0.1 && test "$DB_PORT" = 5433
test "$(realpath .local/library)" = "$PWD/.local/library" && test ! -L .local/library
PG_BIN=/usr/lib/postgresql/17/bin
test -x "$PG_BIN/pg_dump" && test -x "$PG_BIN/pg_restore"
"$PG_BIN/pg_dump" --version
"$PG_BIN/pg_restore" --version
```

Проверка `SOPDS_ROOT_LIB` выполняется отдельно через
`.venv/bin/python manage.py sopds_util getconf SOPDS_ROOT_LIB`; он должен
указывать на `$PWD/.local/library`. `.local/` исключён из Git.

## Снимок БД и тестовой библиотеки

После остановки процессов, из корня checkout и с переменными из блока выше:

```bash
umask 077
mkdir -p .local/backups
BACKUP_BASE="$PWD/.local/backups/sopds-dev-$(date -u +%Y%m%dT%H%M%SZ)"
PGPASSWORD="$DB_PASS" "$PG_BIN/pg_dump" \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
  --format=custom --file="$BACKUP_BASE.dump" "$DB_NAME"
tar -C .local -cpf "$BACKUP_BASE.library.tar" library
```

Храните оба файла вместе с точным Git commit, `uv.lock` и сохранённой вне
репозитория конфигурацией приложения. Снимок БД содержит пользователей,
полки, настройки Constance и задания аннотаций; не публикуйте его и не
добавляйте в Git. Снимок каталога содержит книги, не меняйте исходные файлы.

## Пробное восстановление

Восстанавливайте только в **новую пустую** БД и отдельный каталог. Никогда не
указывайте в качестве назначения текущую `sopds_dev`. После проверок выше:

```bash
RESTORE_DB=sopds_restore_probe
test "$RESTORE_DB" != "$DB_NAME"
test "$(PGPASSWORD="$DB_PASS" psql -X -h "$DB_HOST" -p "$DB_PORT" \
  -U "$DB_USER" -d "$DB_NAME" -Atqc \
  "select count(*) from pg_database where datname = '$RESTORE_DB'")" = 0
PGPASSWORD="$DB_PASS" "$PG_BIN/createdb" \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$RESTORE_DB"
PGPASSWORD="$DB_PASS" "$PG_BIN/pg_restore" \
  -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$RESTORE_DB" \
  --exit-on-error --no-owner --no-privileges "$BACKUP_BASE.dump"
mkdir -p .local/restore-probe
tar -C .local/restore-probe -xpf "$BACKUP_BASE.library.tar"
DB_NAME="$RESTORE_DB" .venv/bin/python manage.py migrate --check
DB_NAME="$RESTORE_DB" .venv/bin/python manage.py sopds_util setconf \
  SOPDS_ROOT_LIB "$PWD/.local/restore-probe/library"
DB_NAME="$RESTORE_DB" .venv/bin/python manage.py sopds_util getconf SOPDS_ROOT_LIB
PGPASSWORD="$DB_PASS" psql -X -h "$DB_HOST" -p "$DB_PORT" \
  -U "$DB_USER" -d "$RESTORE_DB" -Atqc \
  'select (select count(*) from opds_catalog_book), (select count(*) from opds_catalog_bookshelf)'
```

Перед тестовым запуском на восстановленной БД сопоставьте число книг и полок
со снимком, проверьте выборку ID полок и связей и выполните
web/OPDS/download smoke-тест на отдельном адресе.
Пробное восстановление новой dev-БД на искусственных данных выполнено в M06.2;
[результаты и отклонения](m06-restore.md). Базовый тест миграции с
искусственной старой записью проверял цепочку Django отдельно. Миграция
прежней рабочей БД для выбранной новой установки неприменима.

## Откат кандидата

Перед сменой версии остановите web, scanner и annotation worker и сохраните
снимок БД, библиотеки, конфигурации и commit кода. При откате поднимайте
совместимую версию кода **вместе** с соответствующими снимками БД и библиотеки;
не пытайтесь обратить Django-миграции на единственной рабочей БД. Сначала
восстановите снимки в отдельных назначениях и выполните проверки выше, затем
переключайте приложение в запланированное окно обслуживания. Повторный scan
не заменяет восстановление БД: он не вернёт пользовательские полки и данные.

Старое окружение Python 3.7 и копия прежней рабочей БД не предоставлены;
для выбранной новой установки откат на старый checkout неприменим.
