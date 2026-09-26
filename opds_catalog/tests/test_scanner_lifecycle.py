import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase

from constance import config
from opds_catalog.models import Book, bookshelf
from opds_catalog.scanner_process import process_record, scanner_pidfile, stop_scanner
from opds_catalog.sopdscan import opdsScanner


class ScannerIdentityTests(SimpleTestCase):
    def test_stale_and_numeric_pid_files_cannot_signal_an_unrelated_process(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            identity = json.loads(process_record(process.pid))
            identity['start_time'] = '0'
            for record in (str(process.pid), json.dumps(identity)):
                with self.subTest(record=record):
                    with self.assertRaises(CommandError):
                        stop_scanner(record, timeout=1)
                    self.assertIsNone(process.poll())
            stop_scanner(process_record(process.pid), timeout=5)
            self.assertEqual(process.wait(timeout=5), -signal.SIGTERM)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def test_pidfile_is_private_exclusive_and_removed_after_exception(self):
        with TemporaryDirectory() as root:
            path = Path(root, 'scanner.pid')
            with self.assertRaisesRegex(RuntimeError, 'probe'):
                with scanner_pidfile(path):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(json.loads(path.read_text())['pid'], os.getpid())
                    with self.assertRaises(CommandError):
                        with scanner_pidfile(path):
                            self.fail('Concurrent PID-file owner admitted')
                    raise RuntimeError('probe')
            self.assertFalse(path.exists())


class ScannerLifecycleTests(TransactionTestCase):
    def test_sigterm_finishes_scan_and_repeated_run_preserves_book_and_shelf(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            library = root / 'library'
            library.mkdir()
            fixture = Path(__file__).parent / 'data' / '262001.fb2'
            shutil.copyfile(fixture, library / fixture.name)
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_SCANNER_PID = str(root / 'unused.pid')
            config.SOPDS_SCANNER_LOG = str(root / 'unused.log')
            opdsScanner(logging.getLogger('scanner-lifecycle')).scan_all()
            book = Book.objects.get(filename=fixture.name)
            user = User.objects.create_user('lifecycle')
            shelf = bookshelf.objects.create(user=user, book=book)
            original = (book.pk, shelf.pk, shelf.readtime)
            database = connection.settings_dict
            self.assertTrue(str(database['NAME']).startswith('test_'))
            environment = os.environ.copy()
            environment.update(
                DJANGO_SETTINGS_MODULE='sopds.settings',
                TIME_ZONE=settings.TIME_ZONE,
                **{key: str(database[field]) for key, field in (
                    ('DB_NAME', 'NAME'), ('DB_USER', 'USER'), ('DB_PASS', 'PASSWORD'),
                    ('DB_HOST', 'HOST'), ('DB_PORT', 'PORT'),
                )},
            )
            program = """
import sys, time
from pathlib import Path
import django
django.setup()
from django.core.management import call_command
from apscheduler.schedulers.blocking import BlockingScheduler
from opds_catalog.sopdscan import opdsScanner
started, release = map(Path, sys.argv[1:])
original_scan = opdsScanner.scan_all
def slow_scan(self):
    started.write_text('started')
    deadline = time.monotonic() + 10
    while not release.exists():
        if time.monotonic() > deadline:
            raise RuntimeError('Test release timed out')
        time.sleep(0.01)
    return original_scan(self)
opdsScanner.scan_all = slow_scan
original_add = BlockingScheduler.add_job
def immediate_scan(self, func, trigger=None, **kwargs):
    if kwargs.get('id') == 'scan':
        return original_add(self, func, 'date', id='scan')
    return original_add(self, func, trigger, **kwargs)
BlockingScheduler.add_job = immediate_scan
call_command('sopds_scanner', 'run')
"""
            for attempt in range(2):
                started = root / f'started-{attempt}'
                release = root / f'release-{attempt}'
                with (root / f'process-{attempt}.log').open('w+') as log:
                    process = subprocess.Popen(
                        [sys.executable, '-c', program, str(started), str(release)],
                        cwd=settings.BASE_DIR, env=environment,
                        stdout=log, stderr=subprocess.STDOUT,
                    )
                    try:
                        deadline = time.monotonic() + 10
                        while not started.exists() and time.monotonic() < deadline:
                            if process.poll() is not None:
                                log.seek(0)
                                self.fail(log.read()[-3000:])
                            time.sleep(0.01)
                        self.assertTrue(started.exists())
                        process.terminate()
                        time.sleep(0.2)
                        self.assertIsNone(process.poll(), 'Active scan was interrupted')
                        release.write_text('finish')
                        self.assertEqual(process.wait(timeout=10), 0)
                    finally:
                        release.touch()
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                book.refresh_from_db()
                shelf.refresh_from_db()
                self.assertEqual((book.pk, shelf.pk, shelf.readtime), original)
                self.assertEqual(Book.objects.count(), 1)
                self.assertEqual(bookshelf.objects.count(), 1)
                self.assertFalse((root / 'unused.pid').exists())
                self.assertFalse((root / 'unused.log').exists())
