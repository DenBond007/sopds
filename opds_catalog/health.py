"""Operator diagnostics; values never include credentials or exception messages."""
import json
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Min
from django.utils import timezone
from opds_catalog.models import AnnotationTask, Counter, ServiceStatus


def record_service(name, status=None, *, success=False, error=None, details=None):
    values = {'last_seen': timezone.now()}
    if status is not None:
        values['status'] = status
    if success:
        values['last_success'] = timezone.now()
        values['last_error'] = ''
    if error is not None:
        values['last_error'] = error[:160]
    if details is not None:
        values['details'] = details
    ServiceStatus.objects.update_or_create(name=name, defaults=values)


def health_report(*, scan_hours=840, heartbeat_seconds=1200, queue_seconds=900, backup_hours=48):
    now = timezone.now()
    warnings = []
    services = {}
    for name in ('scanner', 'annotations'):
        row = ServiceStatus.objects.filter(pk=name).first()
        services[name] = {'status': row.status if row else 'unknown',
                          'last_seen': row.last_seen.isoformat() if row else None,
                          'last_success': row.last_success.isoformat() if row and row.last_success else None,
                          'last_error': row.last_error if row else '',
                          'details': row.details if row else {}}
        if row is None or now - row.last_seen > timedelta(seconds=heartbeat_seconds):
            warnings.append(name + ': heartbeat missing or stale')
        if row and (row.status in ('error', 'degraded') or row.last_error):
            warnings.append(name + ': ' + row.status)
    last_scan = Counter.objects.get_lastscan()
    if last_scan is None or now - last_scan > timedelta(hours=scan_hours):
        warnings.append('scanner: no recent successful scan')
    queue = dict(AnnotationTask.objects.values_list('status').annotate(total=Count('pk')))
    pending = AnnotationTask.objects.filter(status=AnnotationTask.STATUS_PENDING).aggregate(oldest=Min('next_attempt_at'))['oldest']
    if pending and now - pending > timedelta(seconds=queue_seconds):
        warnings.append('annotations: queue is overdue')
    if queue.get('error', 0):
        warnings.append('annotations: failed jobs need attention')
    expired = AnnotationTask.objects.filter(status='running', lease_until__lt=now).count()
    if expired:
        warnings.append('annotations: expired leases')
    free = shutil.disk_usage(settings.BASE_DIR).free
    if free < getattr(settings, 'SOPDS_MIN_FREE_BYTES', 1024 ** 3):
        warnings.append('disk: less than minimum free space')
    backup = None
    status_file = getattr(settings, 'SOPDS_BACKUP_STATUS_FILE', '')
    if status_file:
        try:
            value = json.loads(Path(status_file).read_text())
            if not isinstance(value, dict):
                raise ValueError('Invalid backup status')
            stamp = timezone.datetime.fromisoformat(value['completed_at'])
            if value.get('verified') is True and timezone.is_aware(stamp):
                backup = {'completed_at':stamp.isoformat(), 'name':value.get('name', ''), 'scope':value.get('scope', '')}
                if now - stamp > timedelta(hours=backup_hours):
                    warnings.append('backup: last verified snapshot is stale')
        except (OSError, ValueError, KeyError, TypeError):
            pass
    backup_dir = Path(settings.SOPDS_BACKUP_DIR)
    if backup is None and backup_dir.is_dir():
        for path in sorted(backup_dir.glob('sopds-*/manifest.json'), reverse=True):
            if path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                value = json.loads(path.read_text())
                if not isinstance(value, dict):
                    continue
                stamp = timezone.datetime.fromisoformat(value['completed_at'])
                if timezone.is_naive(stamp):
                    continue
                if value.get('verified') is not True:
                    continue
                backup = {'completed_at': stamp.isoformat(), 'name': path.parent.name}
                if now - stamp > timedelta(hours=backup_hours):
                    warnings.append('backup: last verified snapshot is stale')
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    if backup is None:
        warnings.append('backup: no verified snapshot visible')
    return {'healthy': not warnings, 'checked_at': now.isoformat(), 'services': services,
            'last_scan': last_scan.isoformat() if last_scan else None,
            'annotations': {'counts': queue, 'oldest_pending': pending.isoformat() if pending else None,
                            'expired_leases': expired},
            'disk_free_bytes': free, 'backup': backup, 'warnings': warnings}
