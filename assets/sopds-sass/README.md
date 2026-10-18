# Сборка SOPDS CSS

Из корня проекта (Node 22.23.3):

```sh
npm --prefix assets/sopds-sass ci --ignore-scripts --omit=optional --no-audit --no-fund
npm --prefix assets/sopds-sass run build
npm --prefix assets/sopds-sass run check
```

Dart Sass закреплён lock-файлом. Выход синхронно записывается в `static/css/sopds.css`
и `sopds_web_backend/static/css/sopds.css`; Django использует первый файл.
Вторая команда `check` компилирует в памяти и отклоняет несвежий CSS.
Foundation 6.2.4 соответствует фактически поставляемому JS; старый Bower-файл
ошибочно указывал 6.1.2. Gulp/Bower перенесены в `docs/legacy`.

Браузерные проверки используют playwright-core и локальный Chromium/Chrome:

```sh
# После настройки отдельной dev PostgreSQL, только одноразовая test-БД:
DB_NAME=sopds_m08 SOPDS_BROWSER_TESTS=1 SOPDS_BROWSER_EXECUTABLE=/usr/bin/google-chrome \
  .venv/bin/python manage.py test opds_catalog.tests.test_browser --noinput
```

В CI браузер устанавливается `npx playwright-core install chromium`; тест сам
создаёт пользователя/книги и поднимает временный HTTP-сервер. Настоящие учётные
данные и рабочая библиотека не нужны. Системные пакеты не устанавливаются агентом.
[Документация Sass](https://sass-lang.com/documentation/js-api/),
[документация Playwright](https://playwright.dev/docs/browsers).
