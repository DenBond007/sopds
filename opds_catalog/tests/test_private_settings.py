import os
import subprocess
import sys

from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.test import RequestFactory, SimpleTestCase, override_settings


class PrivateSettingsTests(SimpleTestCase):
    def _load_settings(self, host, static_root='/srv/sopds/static'):
        environment = os.environ.copy()
        environment.update({
            'SOPDS_SECRET_KEY': 'x' * 64,
            'TIME_ZONE': 'UTC',
            'DB_NAME': 'unused',
            'DB_USER': 'unused',
            'DB_PASS': 'unused',
            'DB_HOST': '127.0.0.1',
            'DB_PORT': '5433',
            'SOPDS_PRIVATE_HOST': host,
            'SOPDS_STATIC_ROOT': static_root,
            'SOPDS_DEBUG': '1',
            'SOPDS_ALLOWED_HOSTS': '*',
        })
        return subprocess.run(
            [sys.executable, '-c',
             'from sopds import settings_private as s; '
             'import os; '
             'assert s.DEBUG is False; '
             'host = os.environ["SOPDS_PRIVATE_HOST"]; '
             'assert s.ALLOWED_HOSTS == [host]; '
             'assert s.CSRF_TRUSTED_ORIGINS == ["https://" + host]; '
             'assert s.SECURE_SSL_REDIRECT; '
             'assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE; '
             'assert s.SECURE_HSTS_SECONDS == 0'],
            env=environment, capture_output=True,
        )

    def test_private_settings_override_dev_http_values(self):
        result = self._load_settings('192.168.1.20')
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_private_settings_uses_changed_ip_or_dns_on_restart(self):
        for host in ('192.168.1.20', '192.168.1.21', 'books.home.arpa'):
            with self.subTest(host=host):
                result = self._load_settings(host)
                self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_private_settings_reject_invalid_host_and_static_root(self):
        for host, root in [('books.local:443', '/srv/sopds/static'),
                           ('192.168.1.20', '/')]:
            with self.subTest(host=host, root=root):
                result = self._load_settings(host, root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b'ImproperlyConfigured', result.stderr)

    @override_settings(
        ALLOWED_HOSTS=['192.168.1.20'],
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        SECURE_SSL_REDIRECT=True,
        SECURE_HSTS_SECONDS=0,
    )
    def test_proxy_scheme_redirect_and_hsts_gate(self):
        middleware = SecurityMiddleware(lambda request: HttpResponse('ok'))
        factory = RequestFactory(HTTP_HOST='192.168.1.20')
        insecure = middleware(factory.get('/opds/'))
        self.assertEqual(insecure.status_code, 301)
        self.assertEqual(insecure['Location'], 'https://192.168.1.20/opds/')

        secure = middleware(factory.get('/opds/', HTTP_X_FORWARDED_PROTO='https'))
        self.assertEqual(secure.status_code, 200)
        self.assertNotIn('Strict-Transport-Security', secure)
