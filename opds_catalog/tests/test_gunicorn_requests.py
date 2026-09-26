"""Real HTTP/process checks on the disposable Django test database."""
import hashlib
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.models import Book


class GunicornRequestsTests(TransactionTestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.library = self.root / 'library'
        self.library.mkdir()
        self.work = self.root / 'work'
        self.marker = self.root / 'converter.json'
        self.converter = self.root / 'converter'
        self.write_converter(0)
        config.SOPDS_AUTH = False
        config.SOPDS_ROOT_LIB = str(self.library)
        config.SOPDS_TEMP_DIR = str(self.work)
        config.SOPDS_FB2TOEPUB = str(self.converter)
        config.SOPDS_TITLE_AS_FILENAME = False
        self.payload = b'Synthetic conversion payload'
        (self.library / 'probe.fb2').write_bytes(self.payload)
        self.book = opdsdb.addbook(
            'probe.fb2', '.', opdsdb.addcattree('.'), 'fb2',
            'Process probe', '', '', 'en', len(self.payload),
        )
        database = connection.settings_dict
        self.assertTrue(str(database['NAME']).startswith('test_'))
        environment = os.environ.copy()
        environment.update(
            DJANGO_SETTINGS_MODULE='sopds.settings',
            SOPDS_ALLOWED_HOSTS='127.0.0.1,localhost', SOPDS_DEBUG='0',
            TIME_ZONE=settings.TIME_ZONE,
            **{key: str(database[field]) for key, field in (
                ('DB_NAME', 'NAME'), ('DB_USER', 'USER'), ('DB_PASS', 'PASSWORD'),
                ('DB_HOST', 'HOST'), ('DB_PORT', 'PORT'),
            )},
        )
        self.log = (self.root / 'gunicorn.log').open('w+')
        self.addCleanup(self.log.close)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            self.url = f'http://127.0.0.1:{listener.getsockname()[1]}'
            self.server = subprocess.Popen(
                [sys.executable, '-m', 'gunicorn', '--config',
                 'infra/gunicorn.conf.py', '--bind', f'fd://{listener.fileno()}',
                 'sopds.wsgi:application'],
                cwd=settings.BASE_DIR, env=environment,
                pass_fds=(listener.fileno(),),
                stdout=self.log, stderr=subprocess.STDOUT,
            )
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                self.log.seek(0)
                self.fail('Gunicorn did not start:\n' + self.log.read()[-3000:])
            try:
                self.fetch('/opds/', timeout=1)
                break
            except (OSError, http.client.HTTPException):
                time.sleep(0.05)
        else:
            self.fail('Gunicorn readiness timed out')

    def write_converter(self, delay):
        self.converter.write_text(
            f'#!{sys.executable}\n'
            'import json, os, shutil, sys, time\nfrom pathlib import Path\n'
            f'Path({str(self.marker)!r}).write_text(json.dumps('
            '{"pid": os.getpid(), "worker": os.getppid()}))\n'
            f'time.sleep({delay})\nshutil.copyfile(sys.argv[1], sys.argv[2])\n'
        )
        self.converter.chmod(0o700)

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)

    def fetch(self, path, timeout=50):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as response:
            return response.read()

    def converter_started(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                return json.loads(self.marker.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.01)
        self.fail('Converter did not start')

    def assert_catalog_responsive(self):
        start = time.monotonic()
        self.assertIn(b'<feed', self.fetch('/opds/', timeout=5))
        self.assertLess(time.monotonic() - start, 5)

    def test_conversion_over_30_seconds_keeps_catalog_available(self):
        self.write_converter(32)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(self.fetch, f'/opds/convert/{self.book.pk}/epub/')
            self.converter_started()
            self.assert_catalog_responsive()
            self.assertEqual(result.result(timeout=45), self.payload)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertTrue(Book.objects.filter(pk=self.book.pk).exists())

    def test_slow_download_keeps_catalog_available_and_preserves_bytes(self):
        payload = b'x' * (8 * 1024 * 1024)
        (self.library / 'probe.fb2').write_bytes(payload)
        self.book.filesize = len(payload)
        self.book.save(update_fields=['filesize'])

        def slow_download():
            digest = hashlib.sha256()
            with urllib.request.urlopen(
                self.url + f'/opds/download/{self.book.pk}/0/', timeout=10,
            ) as response:
                while chunk := response.read(64 * 1024):
                    digest.update(chunk)
                    time.sleep(0.03)
            return digest.digest()

        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(slow_download)
            time.sleep(0.3)
            self.assertFalse(result.done())
            self.assert_catalog_responsive()
            self.assertEqual(result.result(timeout=15), hashlib.sha256(payload).digest())

    def test_worker_abort_cleans_converter_and_temporary_directory(self):
        self.write_converter(60)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(self.fetch, f'/opds/convert/{self.book.pk}/epub/')
            process = self.converter_started()
            worker_status = Path(f'/proc/{process["worker"]}/status').read_text()
            self.assertIn(f'PPid:\t{self.server.pid}\n', worker_status)
            os.kill(process['worker'], signal.SIGABRT)
            with self.assertRaises((OSError, http.client.HTTPException)):
                result.result(timeout=10)
        deadline = time.monotonic() + 5
        while list(self.work.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse(Path(f'/proc/{process["pid"]}').exists())
        self.assert_catalog_responsive()

    def test_graceful_stop_finishes_conversion_and_cleans_files(self):
        self.write_converter(2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(self.fetch, f'/opds/convert/{self.book.pk}/epub/')
            self.converter_started()
            self.server.terminate()
            self.assertEqual(result.result(timeout=10), self.payload)
        self.assertEqual(self.server.wait(timeout=10), 0)
        self.assertEqual(list(self.work.iterdir()), [])
