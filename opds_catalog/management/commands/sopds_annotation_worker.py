import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from opds_catalog.annotation_worker import process_one
from opds_catalog.health import record_service


class Command(BaseCommand):
    help = 'Process on-demand INPX annotation jobs from PostgreSQL.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='Process one available job and exit.')

    def handle(self, *args, **options):
        stopped = False

        def stop(_signum, _frame):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        last_heartbeat = 0
        while not stopped:
            close_old_connections()
            if time.monotonic() - last_heartbeat >= 30:
                record_service('annotations', 'idle')
                last_heartbeat = time.monotonic()
            try:
                worked = process_one()
            except Exception as error:
                record_service('annotations', 'error', error=type(error).__name__)
                raise
            if worked:
                record_service('annotations', 'idle', success=True)
            close_old_connections()
            if options['once']:
                break
            if not worked:
                time.sleep(2)
