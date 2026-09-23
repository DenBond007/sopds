import os
import subprocess
import sys

from django.test import SimpleTestCase


class SettingsSecurityTests(SimpleTestCase):
    def test_missing_secret_prevents_startup(self):
        environment = os.environ.copy()
        environment.pop('SOPDS_SECRET_KEY', None)
        result = subprocess.run(
            [sys.executable, '-c', 'import sopds.settings'],
            env=environment,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'SOPDS_SECRET_KEY', result.stderr)

    def test_default_debug_and_hosts_are_local_only(self):
        environment = os.environ.copy()
        environment['SOPDS_SECRET_KEY'] = 'x' * 64
        environment.pop('SOPDS_DEBUG', None)
        environment.pop('SOPDS_ALLOWED_HOSTS', None)
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'from sopds import settings; '
                'assert settings.DEBUG is False; '
                'assert settings.ALLOWED_HOSTS == ["127.0.0.1", "localhost"]',
            ],
            env=environment,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
