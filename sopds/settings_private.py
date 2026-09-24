"""Settings for the private HTTPS deployment behind a local reverse proxy."""

import ipaddress
import os
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403,F401


def _valid_host(value):
    try:
        ipaddress.IPv4Address(value)
        return True
    except ipaddress.AddressValueError:
        pass
    return all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in value.split(".")
    )


PRIVATE_HOST = os.environ.get("SOPDS_PRIVATE_HOST", "").strip().lower()
if not PRIVATE_HOST or not _valid_host(PRIVATE_HOST):
    raise ImproperlyConfigured(
        "SOPDS_PRIVATE_HOST must be an IPv4 address or hostname without a port"
    )

static_root = Path(os.environ.get("SOPDS_STATIC_ROOT", ""))
if not static_root.is_absolute() or static_root == Path("/"):
    raise ImproperlyConfigured("SOPDS_STATIC_ROOT must be a dedicated absolute path")

DEBUG = False
ALLOWED_HOSTS = [PRIVATE_HOST]
CSRF_TRUSTED_ORIGINS = [f"https://{PRIVATE_HOST}"]
STATIC_ROOT = str(static_root)

# The proxy must be the only process able to reach the loopback WSGI listener.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Enable HSTS only after HTTPS and the OPDS reader are verified on the LAN.
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
