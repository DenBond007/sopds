import json
from django.core.management.base import BaseCommand, CommandError
from opds_catalog.health import health_report


class Command(BaseCommand):
    help = 'Report scanner/worker, queue, disk and backup health; exit nonzero on warnings.'

    def add_arguments(self, parser):
        parser.add_argument('--max-scan-age-hours', type=int, default=840)
        parser.add_argument('--max-heartbeat-age-seconds', type=int, default=1200)
        parser.add_argument('--max-queue-age-seconds', type=int, default=900)
        parser.add_argument('--max-backup-age-hours', type=int, default=48)

    def handle(self, **options):
        limits = [options[key] for key in ('max_scan_age_hours', 'max_heartbeat_age_seconds',
                                          'max_queue_age_seconds', 'max_backup_age_hours')]
        if any(value <= 0 for value in limits):
            raise CommandError('Age limits must be positive')
        report = health_report(scan_hours=limits[0], heartbeat_seconds=limits[1],
                               queue_seconds=limits[2], backup_hours=limits[3])
        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
        if not report['healthy']:
            raise CommandError('Service needs attention; see warnings above')
