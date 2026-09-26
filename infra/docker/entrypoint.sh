#!/bin/sh
set -eu
umask 077
# Schema/data changes are explicit operator commands, never startup side effects.
exec "$@"
