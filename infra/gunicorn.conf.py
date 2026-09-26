"""Native SOPDS service; explicitly load with --config infra/gunicorn.conf.py."""

from opds_catalog.conversion_process import CONVERTER_TIMEOUT_SECONDS


bind = "127.0.0.1:8000"
# Separate processes preserve the existing single-request execution model.
workers = 2
worker_class = "sync"
threads = 1
timeout = CONVERTER_TIMEOUT_SECONDS + 30
graceful_timeout = CONVERTER_TIMEOUT_SECONDS + 30
umask = 0o077
accesslog = "-"
errorlog = "-"
