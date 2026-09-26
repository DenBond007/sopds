# M07: Docker как целевое размещение

Решение пользователя от 2026-09-26. Подготовлены конфигурация и проверки без
Docker daemon; **сборка образов и контейнерный smoke пока не выполнены**.
Эти команды предназначены оператору, имеющему доступ к Docker. Агент не обращается
к Docker socket и не выполняет сборку или запуск контейнеров.

Текущий нативный HTTPS-стенд `https://192.168.88.44:8443/opds/`, его БД,
библиотека и сертификаты сохраняются для проверки физической читалки.
Новые контейнеры используют проект `sopds-container`, свои тома и порт **9443**.
`infra/dev-db/compose.yaml` и шаблоны systemd не изменены.

## Состав

| Сервис | Назначение |
|---|---|
| web | Gunicorn, два sync-worker; timeout 330 с, отдельный HTTP healthcheck |
| scanner | `sopds_scanner run`, foreground, SIGTERM ожидает текущий scan |
| annotations | `sopds_annotation_worker`, очередь в PostgreSQL |
| db | PostgreSQL 17, отдельный том; роль приложения без SUPERUSER/CREATEDB |
| caddy | HTTPS с внутренним CA; static из отдельного тома |
| manage | Явные административные команды, только профиль `tools` |

Приложение работает под UID/GID 10001 с read-only корнем и `/tmp` в tmpfs.
Библиотека — read-only bind, отсутствующий путь не создаётся автоматически.
PostgreSQL запускается под пользователем `postgres`; Caddy — под UID/GID 10001.
Все сервисы сбрасывают capabilities и запрещают повышение привилегий.
Для Caddy снята файловая capability: внутри используется высокий порт 8443.

На хост публикуется только TCP `${SOPDS_PRIVATE_HOST}:9443`. Первый вариант
Compose требует **IPv4 LAN-адрес без порта**, который одновременно используется
в Django, сертификате и привязке порта. DHCP reservation сохраняет этот адрес.
Gunicorn и БД не публикуются; backend-сеть `internal`. Только Caddy подключён
также к ingress-сети. Не подключайте посторонние контейнеры к backend: Django
доверяет `X-Forwarded-Proto` от сервисов в этой сети.

Один образ используется web/scanner/annotations/manage. Dockerfile фиксирует
Python 3.13.15 и uv 0.12.18 по manifest digest; `uv sync --locked --no-dev`
использует `uv.lock` без установки системного Python или обновления зависимостей.
PostgreSQL 17.11 и Caddy 2.11.4 также закреплены по digest. Публичные manifests
проверены 2026-09-26; общие целевые платформы — linux/amd64 и linux/arm64,
работоспособность образа на них ещё проверяется. Обновления требуют явного
обновления digest и повторного smoke.

Build context исключает `.local`, `.git`, env-файлы, БД, книги, тесты и архивы
конвертеров. В образ входит встроенный FB2→HTML preview. Внешние FB2→EPUB/MOBI
конвертеры и Telegram-сервис не включены; соответствующие настройки конвертеров
остаются пустыми. Исходные EPUB/MOBI по-прежнему выдаются без конвертации.

## 1. Отдельное локальное окружение

Из корня checkout создайте новую `.local/container-library` (каталог 0755)
и поместите туда только искусственные или предоставленные тестовые книги.
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
    'SOPDS_HTTPS_PORT': '9443',
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
dc run --rm manage sopds_util setconf SOPDS_AUTH True
dc run --rm manage sopds_util getconf SOPDS_ROOT_LIB
dc run --rm manage sopds_util getconf SOPDS_TEMP_DIR
dc run --rm manage collectstatic --noinput
dc run --rm manage createsuperuser
# Только после подтверждения /library и искусственных входных файлов:
dc run --rm manage sopds_scanner scan
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
  --write-out '%{http_code}\n' https://192.168.88.44:9443/opds/
# curl спросит пароль интерактивно; ожидается 200.
curl --cacert .local/container-root.crt --user sopds_admin \
  --output /dev/null --silent --show-error --write-out '%{http_code}\n' \
  https://192.168.88.44:9443/opds/
```

Подставьте имя созданного пользователя. Установите публичный CA на тестовое
устройство. Не отключайте TLS-проверку и не копируйте приватные ключи CA.
HSTS пока выключен, как на текущем стенде; `check --deploy` поэтому выдаёт W004.

Критерии контейнерного smoke:

1. Все сервисы работают без root; опубликован только выбранный HTTPS-порт.
   Запись в `/library` из контейнера запрещена; исходные хеши книг не меняются.
2. CA и IP проходят проверку, OPDS без входа — 401, с входом — 200. Проверены
   поиск, карточка, точные байты скачанной книги, FB2 preview, static,
   отдельный CSRF-вход web/admin и работа физической читалки.
3. Сканер сохраняет ID при повторном scan; созданная тестовая полка и readtime
   сохраняются. При INPX-сценарии отдельный worker обрабатывает очередь.
4. Остановка scanner во время scan ждёт завершения без повреждения данных.
   Для web проверены длительная конвертация и медленное скачивание через Caddy.
5. `dc restart` сохраняет книги/ID/пользователей/полки, настройки и CA. Проверены
   повторный `migrate --noinput` и запуск с теми же томами. Снять backup БД
   и проверить восстановление в **другой** disposable-проект по
   [операторской инструкции](operations.md), до допуска реальных данных.

HTTP healthcheck проверяет доступность Gunicorn и ожидаемый 200/401. Он не
доказывает сохранность БД или работу сканера и не перезапускает unhealthy-сервис:
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

## Доступные сейчас проверки

`docker compose config` проверен без daemon с искусственными значениями env:
шесть сервисов, изоляция томов/портов, read-only библиотека, non-root, отказ при
отсутствии пяти обязательных параметров. Caddyfile преобразован командой
`caddy adapt`: только HTTPS :8443, admin выключен, установка CA в хост запрещена.
Версии и digest образов сверены с публичными registry manifest. Остальные
результаты M07.1 записаны в [baseline](baseline.md).

Основания: [uv в Docker](https://docs.astral.sh/uv/guides/integration/docker/),
[порядок запуска Compose](https://docs.docker.com/compose/how-tos/startup-order/),
[исходный образ Caddy](https://github.com/caddyserver/caddy-docker/blob/master/2.11/alpine/Dockerfile).
