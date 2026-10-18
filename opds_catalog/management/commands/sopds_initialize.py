"""Initialize the self-contained TrueNAS deployment; never invoked implicitly offline."""
import psycopg2
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from opds_catalog import deployment


class Command(BaseCommand):
    help = 'Apply migrations/static and atomically bootstrap a NEW TrueNAS library.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--check-ready', action='store_true')

    def handle(self, *args, **options):
        try:
            if options['check_ready']:
                deployment.check_ready()
            else:
                deployment.run_initialization()
                self.stdout.write('Initialization succeeded. Existing passwords, settings and library data are preserved.')
        except (DatabaseError, psycopg2.Error, OSError):
            # Driver errors can include connection details or SQL literals.
            raise CommandError('Initialization/readiness failed: check database credentials, migrations and mount permissions. No credentials were changed.') from None
