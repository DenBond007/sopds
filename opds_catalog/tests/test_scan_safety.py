import os
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.models import Book, Catalog, bookshelf
from opds_catalog.management.commands.sopds_scanner import Command as ScannerCommand, SCAN_LOCK_KEYS
from opds_catalog.sopdscan import opdsScanner


class ScanDataSafetyTests(TestCase):
    def test_missing_root_keeps_catalog_book_and_shelf(self):
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook(
            'preserved.fb2', '.', catalog, 'fb2', 'Preserved', '', '', 'en', 100
        )
        user = User.objects.create_user('scan_safety', password='test-password')
        shelf = bookshelf.objects.create(user=user, book=book)

        with TemporaryDirectory() as parent:
            config.SOPDS_ROOT_LIB = os.path.join(parent, 'missing')
            with self.assertRaises(FileNotFoundError):
                opdsScanner().scan_all()

        self.assertEqual(Book.objects.get(pk=book.pk).avail, 2)
        self.assertEqual(Catalog.objects.get(pk=catalog.pk).path, '.')
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

    def test_repeat_scan_preserves_book_id_and_shelf(self):
        fixture = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            shutil.copyfile(fixture, Path(root) / fixture.name)
            config.SOPDS_ROOT_LIB = root
            opdsScanner().scan_all()
            book = Book.objects.get(filename=fixture.name)
            book_id = book.pk
            catalog_id = book.catalog_id
            user = User.objects.create_user('repeat_scan', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            scanner = opdsScanner()
            scanner.scan_all()

        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(Book.objects.get(filename=fixture.name).pk, book_id)
        self.assertEqual(Book.objects.get(pk=book_id).catalog_id, catalog_id)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book_id)
        self.assertEqual(scanner.books_skipped, 1)
        self.assertEqual(scanner.books_updated, 0)

    def test_logical_delete_setting_preserves_missing_book_and_shelf(self):
        fixture = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            copied = Path(root) / fixture.name
            shutil.copyfile(fixture, copied)
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_DELETE_LOGICAL = True
            opdsScanner().scan_all()
            book = Book.objects.get(filename=fixture.name)
            user = User.objects.create_user('logical_delete', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            copied.unlink()
            opdsScanner().scan_all()

            self.assertEqual(Book.objects.get(pk=book.pk).avail, 0)
            self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

            shutil.copyfile(fixture, copied)
            opdsScanner().scan_all()

        self.assertEqual(Book.objects.get(pk=book.pk).avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

    def test_physical_delete_setting_keeps_shelved_book(self):
        fixture = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            copied = Path(root) / fixture.name
            shutil.copyfile(fixture, copied)
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_DELETE_LOGICAL = False
            opdsScanner().scan_all()
            book = Book.objects.get(filename=fixture.name)
            user = User.objects.create_user('shelved_delete', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            copied.unlink()
            opdsScanner().scan_all()

            self.assertEqual(Book.objects.get(pk=book.pk).avail, 0)
            self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

            shutil.copyfile(fixture, copied)
            opdsScanner().scan_all()

        self.assertEqual(Book.objects.get(pk=book.pk).avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

    def test_walk_error_rolls_back_availability_and_keeps_shelf(self):
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook(
            'preserved.fb2', '.', catalog, 'fb2', 'Preserved', '', '', 'en', 100
        )
        user = User.objects.create_user('walk_error', password='test-password')
        shelf = bookshelf.objects.create(user=user, book=book)

        def fail_walk(*args, onerror, **kwargs):
            onerror(PermissionError('test traversal failure'))

        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            command = ScannerCommand()
            command.logger = logging.getLogger('scan-safety-test')
            with patch('opds_catalog.sopdscan.os.walk', side_effect=fail_walk):
                with self.assertRaises(PermissionError):
                    command.scan()

        self.assertEqual(Book.objects.get(pk=book.pk).avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

    def test_changed_book_same_size_updates_metadata_and_keeps_shelf(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        original = source.read_bytes()
        changed = (original
                   .replace(b'The Sanctuary Sparrow', b'The Sanctuary Panther', 1)
                   .replace(b'<first-name>Ellis</first-name>',
                            b'<first-name>Ralph</first-name>', 1)
                   .replace(b'<genre>antique</genre>', b'<genre>fantasy</genre>', 1))
        self.assertEqual(len(changed), len(original))

        with TemporaryDirectory() as root:
            target = Path(root, source.name)
            target.write_bytes(original)
            config.SOPDS_ROOT_LIB = root
            opdsScanner().scan_all()
            book = Book.objects.get(filename=source.name)
            user = User.objects.create_user('changed_book', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)
            original_id = book.pk
            original_registered = book.registerdate
            old_mtime = target.stat().st_mtime_ns

            target.write_bytes(changed)
            os.utime(target, ns=(old_mtime + 1_000_000_000, old_mtime + 1_000_000_000))
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        self.assertEqual(book.title, 'The Sanctuary Panther')
        self.assertEqual(book.pk, original_id)
        self.assertEqual(book.registerdate, original_registered)
        self.assertEqual(book.source_mtime_ns, old_mtime + 1_000_000_000)
        self.assertEqual(list(book.authors.values_list('full_name', flat=True)), ['Peters Ralph'])
        self.assertEqual(list(book.genres.values_list('genre', flat=True)), ['fantasy'])
        self.assertEqual(scanner.books_updated, 1)
        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)

    def test_invalid_changed_book_keeps_metadata_links_and_shelf(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            target = Path(root, source.name)
            shutil.copyfile(source, target)
            config.SOPDS_ROOT_LIB = root
            opdsScanner().scan_all()
            book = Book.objects.get(filename=source.name)
            user = User.objects.create_user('invalid_changed_book', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)
            old_title = book.title
            old_authors = list(book.authors.values_list('pk', flat=True))
            old_genres = list(book.genres.values_list('pk', flat=True))
            old_mtime = book.source_mtime_ns

            target.write_bytes(b'invalid book data')
            os.utime(target, ns=(old_mtime + 1_000_000_000, old_mtime + 1_000_000_000))
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        self.assertEqual(book.title, old_title)
        self.assertEqual(list(book.authors.values_list('pk', flat=True)), old_authors)
        self.assertEqual(list(book.genres.values_list('pk', flat=True)), old_genres)
        self.assertEqual(book.source_mtime_ns, old_mtime)
        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(scanner.bad_books, 1)

    def test_failed_relation_update_rolls_back_changed_book(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            target = Path(root, source.name)
            original = source.read_bytes()
            target.write_bytes(original)
            config.SOPDS_ROOT_LIB = root
            opdsScanner().scan_all()
            book = Book.objects.get(filename=source.name)
            user = User.objects.create_user('failed_relation', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)
            old_authors = list(book.authors.values_list('pk', flat=True))
            old_genres = list(book.genres.values_list('pk', flat=True))
            old_mtime = book.source_mtime_ns

            target.write_bytes(original.replace(
                b'The Sanctuary Sparrow', b'The Sanctuary Panther', 1
            ))
            os.utime(target, ns=(old_mtime + 1_000_000_000, old_mtime + 1_000_000_000))
            with patch('opds_catalog.sopdscan.opdsdb.addbgenre',
                       side_effect=UnicodeEncodeError('utf-8', 'x', 0, 1, 'test')):
                scanner = opdsScanner()
                scanner.scan_all()

        book.refresh_from_db()
        self.assertEqual(book.title, 'The Sanctuary Sparrow')
        self.assertEqual(book.source_mtime_ns, old_mtime)
        self.assertEqual(list(book.authors.values_list('pk', flat=True)), old_authors)
        self.assertEqual(list(book.genres.values_list('pk', flat=True)), old_genres)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(scanner.bad_books, 1)
        self.assertEqual(scanner.books_updated, 0)


class ScannerConcurrencyTests(TransactionTestCase):
    def test_second_command_skips_while_first_scan_is_running(self):
        entered = threading.Event()
        release = threading.Event()
        second_started = threading.Event()
        calls = []

        def slow_scan(_scanner):
            calls.append(threading.get_ident())
            entered.set()
            release.wait(timeout=5)

        def scan_in_thread(command):
            try:
                command.scan()
            finally:
                connections.close_all()

        def second_scan(command):
            second_started.set()
            scan_in_thread(command)

        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            first = ScannerCommand()
            second = ScannerCommand()
            first.logger = second.logger = logging.getLogger('scan-concurrency-test')
            with patch('opds_catalog.sopdscan.opdsScanner.scan_all', slow_scan), \
                 patch('opds_catalog.management.commands.sopds_scanner.Counter.objects.update_known_counters'):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first_job = executor.submit(scan_in_thread, first)
                    self.assertTrue(entered.wait(timeout=2))
                    second_job = executor.submit(second_scan, second)
                    self.assertTrue(second_started.wait(timeout=2))
                    skipped_before_release = False
                    try:
                        second_job.result(timeout=2)
                        skipped_before_release = True
                    except TimeoutError:
                        pass
                    finally:
                        release.set()
                    first_job.result(timeout=2)
                    second_job.result(timeout=2)

        self.assertTrue(skipped_before_release)
        self.assertEqual(len(calls), 1)

    def test_failed_scan_releases_lock_and_allows_retry(self):
        def probe_lock():
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', SCAN_LOCK_KEYS)
                    acquired = cursor.fetchone()[0]
                    if acquired:
                        cursor.execute('SELECT pg_advisory_unlock(%s, %s)', SCAN_LOCK_KEYS)
                return acquired
            finally:
                connections.close_all()

        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            command = ScannerCommand()
            command.logger = logging.getLogger('scan-lock-test')
            with patch('opds_catalog.sopdscan.opdsScanner.scan_all', side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    command.scan()

            self.assertFalse(command.scan_is_active)
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(probe_lock).result(timeout=2))

            with patch('opds_catalog.sopdscan.opdsScanner.scan_all') as scan, \
                 patch('opds_catalog.management.commands.sopds_scanner.Counter.objects.update_known_counters'):
                command.scan()
                scan.assert_called_once()
