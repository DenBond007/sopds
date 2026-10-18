FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
# High ports need no file capabilities; allow cap_drop: ALL at runtime.
RUN setcap -r /usr/bin/caddy \
    && mkdir -p /srv/static \
    && chown -R 10001:10001 /data /config /srv/static \
    && chmod 0700 /data /config /data/caddy /config/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY caddy-healthcheck.sh /usr/local/bin/sopds-caddy-healthcheck
COPY caddy-entrypoint.sh /usr/local/bin/sopds-caddy-entrypoint
USER 10001:10001
ENTRYPOINT ["/bin/sh", "/usr/local/bin/sopds-caddy-entrypoint"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
