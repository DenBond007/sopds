#!/bin/sh
set -eu
umask 077
# Offline-kit commands remain explicit. The single-YAML runtime opts into a
# read-only readiness check, including after daemon restarts (Compose only gates up).
if [ "${SOPDS_REQUIRE_INITIALIZED:-0}" = "1" ]; then
    python manage.py sopds_initialize --check-ready
fi
exec "$@"
