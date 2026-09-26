#!/bin/sh
set -eu
# Exercise TLS with the LAN IP (including clients without SNI), using our CA.
# Resolve locally so health does not depend on host port forwarding/hairpin NAT.
status=$(curl -q --noproxy '*' --silent --show-error --connect-timeout 2 --max-time 5 \
    --cacert /data/caddy/pki/authorities/local/root.crt \
    --resolve "${SOPDS_PRIVATE_HOST}:8443:127.0.0.1" \
    --output /dev/null --write-out '%{http_code}' \
    "https://${SOPDS_PRIVATE_HOST}:8443/opds/")
case "$status" in
    200|401) exit 0 ;;
    *) exit 1 ;;
esac
