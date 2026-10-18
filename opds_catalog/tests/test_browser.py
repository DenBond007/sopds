import os
from pathlib import Path
import secrets
import subprocess
from tempfile import TemporaryDirectory
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from constance import config
from opds_catalog.models import Book, Catalog


@skipUnless(os.environ.get('SOPDS_BROWSER_TESTS') == '1', 'Enable the isolated browser smoke explicitly')
class BrowserSmokeTests(StaticLiveServerTestCase):
    def test_reader_roundtrip_and_search_in_real_chromium(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_TEMP_DIR = root
            config.SOPDS_AUTH = True
            config.SOPDS_DOUBLES_HIDE = False
            config.SOPDS_MAXITEMS = 1
            password = secrets.token_urlsafe(24)
            user = get_user_model().objects.create_user('browser_reader', password=password)
            catalog = Catalog.objects.create(cat_name='.', path='.')
            body = ''.join('<p>Synthetic reading paragraph ' + str(i) + ' ' + 'Sample text. '*30 + '</p>' for i in range(150))
            content = '<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><book-title>Probe</book-title><lang>en</lang></title-info></description><body><section>' + body + '</section></body></FictionBook>'
            for n in range(2):
                filename = f'browser-{n}.fb2'
                Path(root, filename).write_text(content)
                Book.objects.create(catalog=catalog, path='.', filename=filename, format='fb2',
                    title=f'War & Peace +100% {n}', search_title=f'WAR & PEACE +100% {n}',
                    annotation='Synthetic browser probe', avail=2)
            environment = dict(os.environ, SOPDS_E2E_URL=self.live_server_url,
                               SOPDS_E2E_USERNAME=user.username, SOPDS_E2E_PASSWORD=password)
            result = subprocess.run(['node', 'scripts/browser-smoke.mjs'], env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
