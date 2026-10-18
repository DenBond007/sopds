import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from constance import config
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from opds_catalog.health import health_report, record_service
from opds_catalog.models import Counter, ServiceStatus


class HealthTests(TestCase):
    def test_missing_services_and_backup_are_reported(self):
        with TemporaryDirectory() as root, self.settings(SOPDS_BACKUP_DIR=root):
            report = health_report()
        self.assertFalse(report['healthy'])
        self.assertIn('backup: no verified snapshot visible', report['warnings'])
        self.assertIn('scanner: heartbeat missing or stale', report['warnings'])

    def test_recent_signals_and_verified_backup_pass(self):
        record_service('scanner', 'idle', success=True)
        record_service('annotations', 'idle', success=True)
        Counter.objects.update('allbooks', 0)
        with TemporaryDirectory() as root, self.settings(SOPDS_BACKUP_DIR=root, SOPDS_MIN_FREE_BYTES=0):
            p = Path(root, 'sopds-probe'); p.mkdir()
            (p/'manifest.json').write_text(json.dumps({'completed_at':timezone.now().isoformat(), 'verified':True}))
            self.assertTrue(health_report()['healthy'])
            ServiceStatus.objects.filter(pk='scanner').update(last_seen=timezone.now()-timedelta(hours=1))
            self.assertFalse(health_report()['healthy'])

    def test_status_page_requires_staff_and_does_not_disclose_to_normal_reader(self):
        config.SOPDS_AUTH = True
        user = get_user_model().objects.create_user('health_reader')
        url = reverse('web:operations')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 403)
        user.is_staff = True; user.save()
        self.assertContains(self.client.get(url), 'Состояние библиотеки')
