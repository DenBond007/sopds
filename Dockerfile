FROM ghcr.io/astral-sh/uv:0.12.18@sha256:3adc3706091ce7c2fe595e669628caedd6d951551b92b258b7e7dbe06d9440bc AS uv
FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26 AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=sopds.settings_private
WORKDIR /app
RUN groupadd --gid 10001 sopds \
    && useradd --uid 10001 --gid sopds --no-create-home --home-dir /tmp sopds \
    && mkdir -p /library /srv/static \
    && chown sopds:sopds /srv/static
COPY --from=dependencies /app/.venv /app/.venv
COPY manage.py Languages.txt License ./
COPY sopds/ ./sopds/
COPY opds_catalog/ ./opds_catalog/
COPY sopds_web_backend/ ./sopds_web_backend/
COPY constance/ ./constance/
COPY book_tools/ ./book_tools/
COPY static/ ./static/
COPY convert/fb2_to_html.py ./convert/fb2_to_html.py
COPY infra/gunicorn.conf.py ./infra/gunicorn.conf.py
COPY infra/docker/entrypoint.sh infra/docker/healthcheck.py ./infra/docker/
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/app/infra/docker/entrypoint.sh"]
CMD ["gunicorn", "--config", "infra/gunicorn.conf.py", "--bind", "0.0.0.0:8000", "sopds.wsgi:application"]
