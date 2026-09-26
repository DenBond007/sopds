# M07: Docker как целевое размещение

Решение пользователя от 2026-09-26; последующим поручением разрешены сборка и
контейнерная проверка агентом через локальный Docker. **2026-09-27 сборка и
автоматизированный smoke на linux/amd64 прошли**, включая 182/182 общих теста.
Результаты и ограничения — в разделе M07.2 ниже. Физическая читалка и reboot
остаются внешними проверками M06. Docker-контур после проверки остановлен (все 5 сервисов exit 0),
образы и тома сохранены; это ещё не постоянное развёртывание.

Текущий нативный HTTPS-стенд `https://192.168.88.44:8443/opds/`, его БД,
библиотека и сертификаты сохраняются для проверки физической читалки.
Новые контейнеры используют проект `sopds-container`, свои тома и порт **9444**.
Первоначальный 9443 занят Portainer; он не останавливался и не перенастраивался.
`infra/dev-db/compose.yaml` и шаблоны systemd не изменены.

## Состав

| Сервис | Назначение |
|---|---|
| web | Gunicorn, два sync-worker; timeout 330 с, отдельный HTTP healthcheck |
| scanner | `sopds_scanner run`, foreground, SIGTERM ожидает текущий scan |
| annotations | `sopds_annotation_worker`, очередь в PostgreSQL |
| db | PostgreSQL 17, отдельный том; роль приложения без SUPERUSER/CREATEDB |
| caddy | HTTPS с внутренним CA и TLS healthcheck; static из отдельного тома |
| manage | Явные административные команды, только профиль `tools` |

Приложение работает под UID/GID 10001 с read-only корнем и `/tmp` в tmpfs.
Библиотека — read-only bind, отсутствующий путь не создаётся автоматически.
PostgreSQL запускается под пользователем `postgres`; Caddy — под UID/GID 10001.
Все сервисы сбрасывают capabilities и запрещают повышение привилегий.
Для Caddy снята файловая capability: внутри используется высокий порт 8443.
`default_sni` задаёт LAN IP для клиентов, не отправляющих SNI. За Docker NAT
адрес сокета контейнера отличается от LAN IP, поэтому без этой настройки
запрос Python по IP завершался TLS internal error.
Healthcheck Caddy обращается по HTTPS с проверкой собственного CA и IP.

На хост публикуется только TCP `${SOPDS_PRIVATE_HOST}:9444`. Первый вариант
Compose требует **IPv4 LAN-адрес без порта**, который одновременно используется
в Django, сертификате и привязке порта. DHCP reservation сохраняет этот адрес.
Gunicorn и БД не публикуются; backend-сеть `internal`. Только Caddy подключён
также к ingress-сети. Не подключайте посторонние контейнеры к backend: Django
доверяет `X-Forwarded-Proto` от сервисов в этой сети.

Один образ используется web/scanner/annotations/manage. Dockerfile фиксирует
Python 3.13.15 и uv 0.12.18 по manifest digest; `uv sync --locked --no-dev`
использует `uv.lock` без установки системного Python или обновления зависимостей.
PostgreSQL 17.11 и Caddy 2.11.4 также закреплены по digest. Публичные manifests
проверены 2026-09-26; общие целевые платформы — linux/amd64 и linux/arm64.
Linux/amd64 проверен сборкой и запуском; linux/arm64 ещё не проверен.
Обновления требуют явного обновления digest и повторного smoke.

Build context исключает `.local`, `.git`, env-файлы, БД, книги, тесты и архивы
конвертеров. В образ входит встроенный FB2→HTML preview. Внешние FB2→EPUB/MOBI
конвертеры и Telegram-сервис не включены; соответствующие настройки конвертеров
остаются пустыми. Исходные EPUB/MOBI по-прежнему выдаются без конвертации.

## 1. Отдельное локальное окружение

Для новой среды используйте отдельную `.local/container-library` (каталог 0755).
Пример ниже создаёт её вместе с env; затем поместите туда только искусственные
или предоставленные тестовые книги.
UID 10001 должен иметь право чтения файлов и прохода по вложенным каталогам.
Не подключайте библиотеку текущего HTTPS-стенда или рабочую библиотеку.

Скопируйте `infra/docker/.env.example` в `.local/container.env`, задайте права
0600, абсолютный путь `SOPDS_LIBRARY`, свой LAN IPv4 и три независимых случайных
секрета не короче 32 символов. Можно один раз создать файл без вывода секретов:

```bash
python3 - <<'PY'
import os
import secrets
from pathlib import Path
root = Path.cwd().resolve()
(root / '.local').mkdir(exist_ok=True)
library = root / '.local/container-library'
library.mkdir(mode=0o755, exist_ok=False)
values = {
    'SOPDS_PRIVATE_HOST': '192.168.88.44',
    'SOPDS_HTTPS_PORT': '9444',
    'SOPDS_LIBRARY': str(library),
    'TIME_ZONE': 'Europe/Moscow',
    'SOPDS_SECRET_KEY': secrets.token_hex(32),
    'SOPDS_DB_PASSWORD': secrets.token_hex(32),
    'POSTGRES_PASSWORD': secrets.token_hex(32),
}
fd = os.open(root / '.local/container.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as out:
    for key, value in values.items():
        out.write(f"{key}='{value}'\n")
PY
```

Этот пример намеренно не перезаписывает существующие пути. Проверьте LAN-адрес
в созданном файле перед запуском. Не выводите файл, `docker inspect` или полный
`compose config` с реальными секретами. Env передаётся только при запуске,
не через build args и не внутри слоёв образа. Владельцы Docker daemon имеют доступ
к окружению контейнеров; доступ к нему должен быть ограничен.

Далее определите сокращение в той же оболочке:

```bash
dc() {
  docker compose --env-file "$PWD/.local/container.env" \
    -f "$PWD/infra/docker/compose.yaml" "$@"
}
dc --profile tools config --quiet
dc build web caddy
```

На проверенном хосте общий multiarch pull PostgreSQL зависал на Docker Hub.
Публичный manifest и SHA256 были проверены, после чего явно загружен **тот же**
linux/amd64-образ из закреплённого index. Для воспроизведения этого варианта:

```bash
docker pull --platform linux/amd64 \
  postgres:17.11-bookworm@sha256:91eb910c44c7ed13f7f1a4ccadaa9ca72ef14cddc04cacb6e070e48eb44731a3
dc() {
  docker compose --env-file "$PWD/.local/container.env" \
    -f "$PWD/infra/docker/compose.yaml" \
    -f "$PWD/infra/docker/compose.amd64.yaml" "$@"
}
```

Этот override применяется только на linux/amd64 и ожидает уже загруженный образ
(`pull_policy: never`). Основной Compose сохраняет multiarch digest для других
платформ. Все остальные настройки совпадают; сравнение `compose config`
подтверждено. Дальнейшие команды используют выбранную функцию `dc`.


## 2. Явная подготовка новой БД

Не запускайте `up` всего проекта до этих шагов. При первом старте пустого тома
официальный entrypoint PostgreSQL создаёт БД; `init-db.sh` создаёт отдельную роль
`sopds` и назначает её владельцем БД. Пароль передаётся внутри psql через
`\getenv`, без SQL-интерполяции оболочкой и без аргумента командной строки.
На непустом томе скрипт повторно не выполняется. Изменение пароля в env-файле
само по себе **не меняет** пароль уже созданной роли.

```bash
dc up -d --wait db
dc run --rm manage check
dc run --rm manage migrate --noinput
# Только для НОВОЙ БД: исходный справочник жанров; не повторять поверх своего.
dc run --rm manage loaddata genre.json
dc run --rm manage sopds_util setconf SOPDS_ROOT_LIB /library
dc run --rm manage sopds_util setconf SOPDS_TEMP_DIR /tmp/sopds
# Одноразовый scan использует legacy FileHandler; корень образа read-only.
dc run --rm manage sopds_util setconf SOPDS_SCANNER_LOG /tmp/sopds-scanner.log
dc run --rm manage sopds_util setconf SOPDS_AUTH True
dc run --rm manage sopds_util getconf SOPDS_ROOT_LIB
dc run --rm manage sopds_util getconf SOPDS_TEMP_DIR
dc run --rm manage collectstatic --noinput
dc run --rm manage createsuperuser
# Только после подтверждения /library и искусственных входных файлов:
dc run --rm manage sopds_scanner scan --verbose
dc run --rm manage sopds_util info
dc up -d --wait web scanner annotations caddy
```

Запуски web/worker не выполняют миграции, загрузку fixtures или очистку данных.
`manage` использует ту же роль приложения и библиотеку; `collectstatic` пишет
в отдельный том, который Caddy читает. Команда `sopds_util clear` здесь не нужна.
Администратора используйте для настройки; для повседневного чтения создайте
обычного пользователя через Django admin.

## 3. HTTPS и приёмка M07.2

CA контейнерного Caddy отличается от CA нативного стенда. После первого запуска
экспортируйте **только публичный сертификат**:

```bash
dc cp caddy:/data/caddy/pki/authorities/local/root.crt .local/container-root.crt
# Подставьте тот же LAN IPv4. Анонимный запрос должен получить 401.
curl --cacert .local/container-root.crt --output /dev/null --silent --show-error \
  --write-out '%{http_code}\n' https://192.168.88.44:9444/opds/
# curl спросит пароль интерактивно; ожидается 200.
curl --cacert .local/container-root.crt --user sopds_admin \
  --output /dev/null --silent --show-error --write-out '%{http_code}\n' \
  https://192.168.88.44:9444/opds/
```

Подставьте имя созданного пользователя. Установите публичный CA на тестовое
устройство. Не отключайте TLS-проверку и не копируйте приватные ключи CA.
HSTS пока выключен, как на текущем стенде; `check --deploy` поэтому выдаёт W004.

Критерии контейнерного smoke:

1. Все сервисы работают без root; опубликован только выбранный HTTPS-порт.
   Запись в `/library` из контейнера запрещена; исходные хеши книг не меняются.
2. CA и IP проходят проверку, OPDS без входа — 401, с входом — 200. Проверены
   поиск, карточка, точные байты скачанной книги, FB2 preview, static,
   отдельный CSRF-вход web/admin. Работа физической читалки остаётся отдельной
   внешней проверкой M06.4 на сохранённом стенде.
3. Сканер сохраняет ID при повторном scan; созданная тестовая полка и readtime
   сохраняются. При INPX-сценарии отдельный worker обрабатывает очередь.
4. Остановка scanner во время scan ждёт завершения без повреждения данных.
   Для web проверены длительная конвертация и медленное скачивание через Caddy.
5. `dc restart` сохраняет книги/ID/пользователей/полки, настройки и CA. Проверены
   повторный `migrate --noinput` и запуск с теми же томами. Снять backup БД
   и проверить восстановление в отдельную одноразовую БД по
   [операторской инструкции](operations.md), до допуска реальных данных.

HTTP healthcheck web проверяет Gunicorn и ожидаемый 200/401; healthcheck Caddy
дополнительно проверяет TLS/CA/IP через loopback контейнера. Они не доказывают
сохранность БД или работу сканера и не перезапускают unhealthy-сервис:
`unless-stopped` действует при завершении процесса. Логи доступны через
`dc logs --tail 100`; json-file ограничен 3×10 МиБ на сервис.

## Эксплуатация после приёмки

Остановка: `dc stop`. Удаление контейнеров с сохранением томов: `dc down`.
Не используйте `down -v` для среды с данными. При обновлении сначала backup и
проверка восстановления, затем остановка приложения, сборка, явные миграции и
collectstatic, запуск и smoke. Откат миграций нельзя заменять сменой образа.

Gunicorn и Caddy имеют 330 с для завершения, Docker даёт каждому 345 с. Scanner имеет
300 с, annotations — 60 с; предел scanner надо увеличить по измеренному полному
сканированию библиотеки. По истечении grace period Docker может послать SIGKILL.
Tmpfs приложения ограничен 512 МиБ: большие книги/конвертации требуют отдельной
проверки и осознанного изменения лимита. Реальная библиотека пока не допущена.

Системный менеджер хоста запускает Docker Engine; отдельный systemd unit для
каждого контейнерного процесса не нужен. Проверка автозапуска после reboot и
выбор постоянного порта остаются частью эксплуатации после M07.2.

## Проверки подготовки M07.1

`docker compose config` проверен без daemon с искусственными значениями env:
шесть сервисов, изоляция томов/портов, read-only библиотека, non-root, отказ при
отсутствии пяти обязательных параметров. Caddyfile преобразован командой
`caddy adapt`: только HTTPS :8443, admin выключен, установка CA в хост запрещена.
Версии и digest образов сверены с публичными registry manifest. Остальные
результаты M07.1 записаны в [baseline](baseline.md).

Основания: [uv в Docker](https://docs.astral.sh/uv/guides/integration/docker/),
[порядок запуска Compose](https://docs.docker.com/compose/how-tos/startup-order/),
[исходный образ Caddy](https://github.com/caddyserver/caddy-docker/blob/master/2.11/alpine/Dockerfile).


## M07.2: фактическая контейнерная проверка 2026-09-27

- Docker 29.8.1, локальный context `default`, linux/amd64. Собраны app и Caddy;
  подтверждены Python 3.13.15 / Django 5.2.17 / PostgreSQL 17.11 / Caddy 2.11.4.
- В образе нет `.local`, `.git`, env-файлов, тестовых книг/fixtures и старых
  бинарных конвертеров; все 6 миграций приложения присутствуют. Все процессы
  запускаются без root, запись в корень app и `/library` запрещена.
- Новая БД `sopds`, отдельная искусственная библиотека: два FB2, включая >8 МиБ,
  и один FB2 в ZIP/INPX. Настроены /library, /tmp/sopds и лог одноразового scan
  /tmp/sopds-scanner.log. Миграции, genres, collectstatic и сканирование прошли.
- TLS без отключения проверки CA/IP: 401/200, search, точные bytes скачиваний,
  FB2 preview/CSP, отдельный INPX worker, CSRF 403/успешный web/admin, static.
  Медленное скачивание >8 МиБ не блокировало параллельный каталог.
- Весь набор **182/182 за 118.011 с** выполнен внутри app image. Тестовые файлы
  смонтированы read-only только в одноразовый runner; exec в /tmp разрешён только
  ему для искусственных converter fixtures. Его CREATEDB-роль и обе test-БД
  удалены. Runtime-роль sopds осталась без SUPERUSER/CREATEDB/CREATEROLE.
- Повторный scan и остановка scanner при активной работе сохранили ID,
  полку/readtime. SIGTERM ждал завершения реального scan и вернул exit 0.
- Перезапуск всех пяти сервисов сохранил хеши строк всех таблиц и публичный CA.
  Повторный migrate не изменил данные. Backup восстановлен в отдельную БД;
  все таблицы совпали, восстановленная БД удалена, библиотека не менялась.
- HTTPS-preview с управляемой задержкой 32 с завершился за 33.2 с при SIGTERM
  web и отдельно Caddy. Каталог оставался доступен. Временные read-only mounts
  с задержкой сняты, исходный web image восстановлен.

Локальные результаты находятся в `.local/logs/m07-*.log`. Backup БД и библиотеки:
`.local/backups/m07-container-20260927/` (0700, файлы 0600). Секреты новой тестовой
среды — `.local/container.env` и `.local/m07/credentials.json` (0600), их содержимое
не выводилось. Публичный CA контейнерного контура — `.local/m07/root.crt`.
Оставшиеся ограничения: реальная читалка, reboot и linux/arm64 не проверены;
публикация коммитов не выполнялась. Текущий нативный стенд 8443 сохраняется.

Для повторного запуска созданного тестового контура **без повторной инициализации**
выберите функцию dc с `compose.amd64.yaml` выше, затем `dc up -d --wait`.
Для остановки — `dc stop`; тома сохраняются. Не повторяйте создание суперпользователя,
загрузку genres или генерацию env поверх уже созданного контура.
