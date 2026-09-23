# Ubuntu 24.04: подготовка окружения SOPDS

Подготовлено 23 сентября 2026 года. Это заготовка для разработки, а не обновлённое
приложение. Скрипт подготовки не устанавливает зависимости SOPDS и не меняет БД.
Команды выполнять последовательно; после ошибки не переходить к следующему шагу.
Все команды — внутри VM, кроме явно отмеченного переноса архива с Mac.
Системные команды выполняет человек; Codex запускать без sudo.

## 1. Инструменты

```bash
sudo apt update
sudo apt install -y git curl ca-certificates unzip tmux python3 \
  build-essential libpq-dev postgresql-client
```

GitHub CLI лучше установить из официального репозитория, а не брать старую версию
из исходного набора пакетов Ubuntu 24.04. Если официальный репозиторий уже настроен,
не создавать второй набор sources — достаточно обновления существующей установки.

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
sudo chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' \
  "$(dpkg --print-architecture)" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
sudo apt update
sudo apt install -y gh
```

uv установить, если он отсутствует. Сначала можно просмотреть скачанный установщик.

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/sopds-uv-install.sh
less /tmp/sopds-uv-install.sh
sh /tmp/sopds-uv-install.sh
. "$HOME/.local/bin/env"
uv python install 3.13
```

Это установка ЦЕЛЕВОГО Python, а не попытка запустить в нём старый SOPDS.
Не заменять `/usr/bin/python3`.

```bash
whoami
uname -m
codex --version
codex login status
gh --version
uv --version
```

Если CLI не авторизован: `codex login --device-auth` и вход через свой браузер.
Авторизация Codex и GitHub — две разные операции.

## 2. GitHub без отдельного SSH-ключа и PAT

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git --hostname github.com
gh auth status
gh api user --jq .login
```

Для показанного HTTPS-сценария отдельный SSH-ключ GitHub не нужен. Не вставлять токены
в чат, Git remote URL, файлы проекта и историю команд. gh использует хранилище
учётных данных, а при его отсутствии может хранить токен в обычном файле; посмотреть
место хранения через `gh auth status`, не выводить сам токен.
AGENTS.md не изолирует GitHub-права технически. Для строгого ограничения агенту нужен
отдельный аккаунт либо fine-grained PAT только нужного репозитория; это отдельный
режим настройки, не дополнение «на всякий случай» к выбранному браузерному входу.

## 3. Форк и рабочий репозиторий

Если `ichbinkirgiz` — ваш аккаунт, использовать:

```bash
WORK_REPO=ichbinkirgiz/sopds
```

Иначе создать свой форк (не создавать заново уже существующий):

```bash
gh repo fork ichbinkirgiz/sopds --clone=false --remote=false
WORK_REPO="$(gh api user --jq .login)/sopds"
```

Если gh сообщает о существующем форке под другим именем, взять именно показанное
OWNER/REPO в WORK_REPO. Не использовать другой несвязанный репозиторий с именем sopds.

```bash
mkdir -p ~/src
git clone "https://github.com/${WORK_REPO}.git" ~/src/sopds
cd ~/src/sopds
gh repo set-default "$WORK_REPO"
git config sopds.github-repo "$WORK_REPO"
git config sopds.base-branch \
  "$(gh repo view "$WORK_REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
```

Если checkout уже существует, не клонировать поверх него: проверить Git/remotes,
выбрать существующую рабочую копию и сохранить чужие изменения.
Только для своего нового форка, созданного от ichbinkirgiz/sopds:

```bash
git remote add upstream https://github.com/ichbinkirgiz/sopds.git
```

Если upstream существует, сначала посмотреть его значение; не перезаписывать вслепую.
В дальнейшем не синхронизировать модернизируемую ветку с upstream автоматическими reset.

```bash
git config user.name "ВАШЕ ИМЯ"
git config user.email "ВАШ ПОДТВЕРЖДЁННЫЙ ИЛИ NOREPLY EMAIL GITHUB"
git remote -v
git status --short --branch
git switch -c modernization/sopds
git tag "modernization-start-$(date +%Y%m%d-%H%M%S)"
```

## 4. Разместить файлы комплекта

Скачать sopds-codex-start.zip в домашний каталог VM и распаковать без перезаписи:

```bash
unzip -n ~/sopds-codex-start.zip -d ~/src/sopds
cd ~/src/sopds
```

Если AGENTS.md/PLAN.md/STATE.md уже есть, объединить инструкции и сохранить прогресс;
ключ -n специально не заменяет их. При необходимости сначала распаковать комплект
в отдельную папку и сравнить нужные файлы.

## 5. Docker Engine — только для dev-БД

Проверить существующую установку:

```bash
sudo docker version
sudo docker compose version
```

Если Docker работает, пропустить установку ниже. Не удалять существующие контейнеры,
volumes, настройки и несовместимые пакеты автоматически. При конфликтах пакетов
свериться с официальной инструкцией Docker перед изменениями.

Для чистой Ubuntu 24.04 без существующей установки Docker:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "$(dpkg --print-architecture)" \
  | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

Не добавлять пользователя Codex в группу docker: для rootful Docker это фактически
права root. Если командами с sudo управляет отдельный администратор, он выполняет
эти операции; самому пользователю Codex административные права не требуются.

## 6. Создать локальные файлы и запустить БД

Проверить скрипты перед выполнением, затем:

```bash
cd ~/src/sopds
python3 scripts/dev/prepare-local.py
sudo docker compose --env-file .local/postgres.env \
  -f infra/dev-db/compose.yaml up -d --wait
```

Это отдельный Compose-проект `sopds-modernization-dev` и отдельный volume.
Не использовать уже существующий проект/volume с таким именем, если его назначение
не подтверждено. Не подключать к нему рабочую БД.

Создаётся PostgreSQL 17, порт `127.0.0.1:5433`. Приложение использует роль `sopds_dev`
без SUPERUSER и CREATEROLE, но с CREATEDB для тестовых БД Django.
Создание роли и БД происходит только при первом запуске пустого volume.
Повторный запуск prepare-local.py не заменяет пароли. Удаление env-файлов не сбрасывает
пароли внутри уже созданного volume. При ошибке инициализации не выполнять down -v:
проверить логи и исправить конкретную причину.

Проверка от имени приложения без вывода пароля:

```bash
set -a
. .local/dev.env
set +a
PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" \
  -U "$DB_USER" -d "$DB_NAME" \
  -c 'SELECT current_database(), current_user;'
```

Ожидаются `sopds_dev` и `sopds_dev`.
Команда администратора для безопасной фиксации digest образа:

```bash
sudo docker image inspect postgres:17 --format '{{index .RepoDigests 0}}' \
  > .local/postgres-image.txt
```

Шаблон использует тег `postgres:17` для первого запуска. Зафиксировать полученный
digest в baseline, затем использовать закреплённый образ в воспроизводимом контуре.
Не выводить полный docker inspect: он может содержать переменные с паролями.

## 7. Данные и первый коммит

Положить несколько тестовых книг/архивов в `.local/library`. Только копии;
не подключать production. До реальной миграции понадобится backup старой БД и
проверка его восстановления, но для первого анализа достаточно пустой dev-БД/фикстур.
SOPDS_DEV_LIBRARY в dev.env — только подсказка для dev-launcher. Codex должен
явно настроить прежний SOPDS_ROOT_LIB перед тестовым scan.

```bash
git check-ignore .local/dev.env .local/postgres.env
git status --short
git add AGENTS.md docs/modernization infra/dev-db scripts/dev/prepare-local.py
git diff --cached --stat
git diff --cached
# Коммит выполнять после просмотра индекса и проверки отсутствия секретов.
git commit -m "chore: add safe modernization workspace"
```

## 8. Codex

Открыть один пишущий агент. Desktop можно использовать для просмотра,
но не запускать второй процесс изменения того же checkout.
При работе по SSH можно сначала открыть `tmux new -s sopds-codex`.

```bash
cd ~/src/sopds
codex --sandbox workspace-write --ask-for-approval on-request \
  -c agents.enabled=false -c 'model_reasoning_effort="medium"'
```

Проверить `/status`: выбранный режим, модель и расход. Если medium не поддерживается
выбранной моделью, убрать этот override и выбрать доступный уровень через `/model`.
Не использовать full-access/yolo для устранения ошибки сети. Доступ к PostgreSQL
и загрузка зависимостей могут требовать отдельных разрешений для конкретных команд.
Нужные локальные env-переменные Codex загружает для команды сам, не печатая их.

Первое задание: раздел M00 из PROMPTS.md. После его выполнения человек проверяет
результат и разрешает M01–M05 отдельной целью. Не нужно вставлять всю переписку.
Для новой сессии после остановки:

```bash
cd ~/src/sopds
codex resume --last --sandbox workspace-write --ask-for-approval on-request \
  -c agents.enabled=false -c 'model_reasoning_effort="medium"'
```

Если сессия tmux ещё работает, вернуться в неё через `tmux attach -t sopds-codex`,
не запускать дополнительный Codex. Отключиться от tmux: Ctrl-b, затем d.
Сам tmux не предотвращает выключение/сон VM и хоста.

## 9. Публикация после проверки человеком

Не выполнять команды публикации, пока diff, тесты и возможный автодеплой не проверены.
Они отправляют код на GitHub, а не обновляют production вручную.

```bash
cd ~/src/sopds
WORK_REPO=$(git config --get sopds.github-repo)
BASE_BRANCH=$(git config --get sopds.base-branch)
git status --short
git log --oneline -10
git diff "origin/$BASE_BRANCH...HEAD" --stat
git diff "origin/$BASE_BRANCH...HEAD"
```

После подтверждения результата и отсутствия секретов:

```bash
git push -u origin modernization/sopds
gh pr create --repo "$WORK_REPO" --base "$BASE_BRANCH" \
  --head modernization/sopds --draft \
  --title "SOPDS modernization" \
  --body "Модернизация в отдельной ветке. План, проверки и ограничения: docs/modernization."
```

PR направлен в выбранный собственный репозиторий, не автоматически в mitshel/sopds.
Повторно создавать тот же PR не нужно. Следующие проверенные коммиты отправляются
`git push origin modernization/sopds`. Merge и production-перенос — отдельное решение.

## Первичные источники

- GitHub CLI installation: https://github.com/cli/cli/blob/trunk/docs/install_linux.md
- GitHub CLI authentication: https://cli.github.com/manual/gh_auth_login
- Git credentials helper: https://cli.github.com/manual/gh_auth_setup-git
- Fork: https://cli.github.com/manual/gh_repo_fork
- PR: https://cli.github.com/manual/gh_pr_create
- Docker Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- Docker privileges: https://docs.docker.com/engine/install/linux-postinstall/
- PostgreSQL image: https://github.com/docker-library/docs/tree/master/postgres
- uv: https://docs.astral.sh/uv/getting-started/installation/
- Codex instructions: https://developers.openai.com/codex/guides/agents-md
- Codex commands: https://developers.openai.com/codex/cli/reference
- Codex permissions: https://learn.chatgpt.com/docs/agent-approvals-security
