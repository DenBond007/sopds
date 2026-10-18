#!/bin/sh
set -eu
if [ "${SOPDS_REQUIRE_INITIALIZED:-0}" = "1" ]; then
    test "$(cat /srv/static/.sopds-ready)" = "$SOPDS_RELEASE"
    # Docker daemon restarts do not enforce Compose dependencies. Check the
    # guarded web process as well as the release's static completion marker.
    status=$(curl -q --noproxy '*' --silent --show-error --connect-timeout 2 --max-time 5 \
        --header "Host: ${SOPDS_PRIVATE_HOST}" --header 'X-Forwarded-Proto: https' \
        --output /dev/null --write-out '%{http_code}' http://web:8000/opds/)
    case "$status" in
        200|401) ;;
        *) exit 1 ;;
    esac
fi
exec "$@"
