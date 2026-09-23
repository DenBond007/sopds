import os
import logging
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from constance import config
from book_tools.format import create_bookfile
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
            first_scan_id = book.last_seen_scan_id
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
        self.assertIsNotNone(first_scan_id)
        self.assertEqual(Book.objects.get(pk=book_id).last_seen_scan_id, scanner.scan_id)
        self.assertNotEqual(first_scan_id, scanner.scan_id)

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

    def test_same_size_changed_zip_is_not_skipped(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        original = source.read_bytes()
        changed = original.replace(b'The Sanctuary Sparrow', b'The Sanctuary Panther', 1)
        self.assertEqual(len(changed), len(original))

        with TemporaryDirectory() as root:
            target = Path(root, 'books.zip')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_ZIPSCAN = True
            for index, payload in enumerate((original, changed)):
                with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr(source.name, payload)
                if index == 0:
                    archive_size = target.stat().st_size
                    archive_mtime = target.stat().st_mtime_ns
                    opdsScanner().scan_all()
                    book = Book.objects.get(filename=source.name)
                    user = User.objects.create_user('changed_zip', password='test-password')
                    shelf = bookshelf.objects.create(user=user, book=book)
                else:
                    self.assertEqual(target.stat().st_size, archive_size)
                    os.utime(target, ns=(archive_mtime + 1_000_000_000,
                                         archive_mtime + 1_000_000_000))
                    opdsScanner().scan_all()
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        self.assertEqual(book.title, 'The Sanctuary Panther')
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(scanner.arch_skipped, 1)
        self.assertEqual(book.last_seen_scan_id, scanner.scan_id)

    def test_same_size_changed_inpx_is_not_skipped(self):
        def inp_line(title, author, genre, series):
            fields = [author, genre, title, series, '1', '123', '100',
                      'id', '', 'fb2', '2020-01-01', 'en']
            return '\x04'.join(fields).encode('utf-8') + b'\n'

        with TemporaryDirectory() as root:
            target = Path(root, 'index.inpx')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_INPX_ENABLE = True
            config.SOPDS_INPX_SKIP_UNCHANGED = True
            config.SOPDS_INPX_TEST_ZIP = False
            config.SOPDS_INPX_TEST_FILES = False
            variants = (
                ('Old Name', 'Doe, Jane', 'fantasy', 'Old Series'),
                ('New Name', 'Roe, Mary', 'mystery', 'New Series'),
            )
            for title, author, genre, series in variants:
                with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr('books.inp', inp_line(title, author, genre, series))
                if title == 'Old Name':
                    archive_size = target.stat().st_size
                    archive_mtime = target.stat().st_mtime_ns
                    opdsScanner().scan_all()
                    book = Book.objects.get(filename='123.fb2')
                    original_registered = book.registerdate
                    user = User.objects.create_user('changed_inpx', password='test-password')
                    shelf = bookshelf.objects.create(user=user, book=book)
                else:
                    self.assertEqual(target.stat().st_size, archive_size)
                    os.utime(target, ns=(archive_mtime + 1_000_000_000,
                                         archive_mtime + 1_000_000_000))
                    opdsScanner().scan_all()
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        self.assertEqual(book.title, 'New Name')
        self.assertEqual(book.registerdate, original_registered)
        self.assertEqual(list(book.authors.values_list('full_name', flat=True)), ['Roe  Mary'])
        self.assertEqual(list(book.genres.values_list('genre', flat=True)), ['mystery'])
        self.assertEqual(list(book.series.values_list('ser', flat=True)), ['New Series'])
        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(scanner.books_updated, 0)
        self.assertEqual(book.last_seen_scan_id, scanner.scan_id)

    def test_corrupt_changed_zip_keeps_existing_book_and_shelf(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            target = Path(root, 'books.zip')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_ZIPSCAN = True
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(source.name, source.read_bytes())
            opdsScanner().scan_all()
            book = Book.objects.get(filename=source.name)
            catalog = book.catalog
            original_mtime = catalog.source_mtime_ns
            user = User.objects.create_user('corrupt_zip', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            target.write_bytes(b'invalid ZIP archive')
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        catalog.refresh_from_db()
        self.assertEqual(book.title, 'The Sanctuary Sparrow')
        self.assertEqual(book.avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(catalog.source_mtime_ns, original_mtime)
        self.assertEqual(scanner.bad_archives, 1)

    def test_oversize_zip_member_is_not_parsed_and_keeps_existing_shelf(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            target = Path(root, 'books.zip')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_ZIPSCAN = True
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(source.name, source.read_bytes())
            opdsScanner().scan_all()
            book = Book.objects.get(filename=source.name)
            catalog = book.catalog
            original_mtime = catalog.source_mtime_ns
            user = User.objects.create_user('oversize_zip', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(source.name, source.read_bytes())
                archive.writestr('huge.fb2', b'x' * 500001)
            with patch('opds_catalog.sopdscan.MAX_SCANNED_ARCHIVE_BOOK_BYTES', 500000,
                       create=True), \
                 patch('opds_catalog.sopdscan.create_bookfile',
                       wraps=create_bookfile) as parse:
                scanner = opdsScanner()
                scanner.scan_all()

        self.assertEqual(parse.call_count, 1)
        book.refresh_from_db()
        catalog.refresh_from_db()
        self.assertEqual(book.title, 'The Sanctuary Sparrow')
        self.assertEqual(book.avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(catalog.source_mtime_ns, original_mtime)
        self.assertEqual(scanner.bad_archives, 1)

    def test_zip_book_parse_error_rolls_back_earlier_updates(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            target = Path(root, 'books.zip')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_ZIPSCAN = True
            original = source.read_bytes()
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(source.name, original)
            opdsScanner().scan_all()
            book = Book.objects.get(filename=source.name)
            catalog = book.catalog
            original_mtime = catalog.source_mtime_ns

            changed = original.replace(b'The Sanctuary Sparrow',
                                       b'The Sanctuary Panther', 1)
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(source.name, changed)
                archive.writestr('broken.fb2', b'invalid book data')
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        catalog.refresh_from_db()
        self.assertEqual(book.title, 'The Sanctuary Sparrow')
        self.assertEqual(book.avail, 2)
        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(catalog.source_mtime_ns, original_mtime)
        self.assertEqual(scanner.bad_books, 1)
        self.assertEqual(scanner.bad_archives, 1)
        self.assertEqual(scanner.books_updated, 0)

    def test_skipped_zip_does_not_revive_removed_shelved_book(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            target = Path(root, 'books.zip')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_ZIPSCAN = True
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(source.name, source.read_bytes())
                archive.writestr('removed.fb2', source.read_bytes())
            opdsScanner().scan_all()
            removed = Book.objects.get(filename='removed.fb2')
            user = User.objects.create_user('removed_from_zip', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=removed)

            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(source.name, source.read_bytes())
            opdsScanner().scan_all()
            self.assertEqual(Book.objects.get(pk=removed.pk).avail, 0)
            scanner = opdsScanner()
            scanner.scan_all()

        self.assertEqual(Book.objects.get(pk=removed.pk).avail, 0)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, removed.pk)
        self.assertEqual(scanner.arch_skipped, 1)

    def test_corrupt_changed_inpx_keeps_existing_book_and_shelf(self):
        fields = ['Doe, Jane', 'fantasy', 'Old Name', '', '', '123', '100',
                  'id', '', 'fb2', '2020-01-01', 'en']
        with TemporaryDirectory() as root:
            target = Path(root, 'index.inpx')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_INPX_ENABLE = True
            config.SOPDS_INPX_SKIP_UNCHANGED = True
            config.SOPDS_INPX_TEST_ZIP = False
            config.SOPDS_INPX_TEST_FILES = False
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('books.inp', '\x04'.join(fields).encode() + b'\n')
            opdsScanner().scan_all()
            book = Book.objects.get(filename='123.fb2')
            catalog = book.catalog.parent.parent
            original_mtime = catalog.source_mtime_ns
            user = User.objects.create_user('corrupt_inpx', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            target.write_bytes(b'invalid INPX archive')
            scanner = opdsScanner()
            scanner.scan_all()

        book.refresh_from_db()
        catalog.refresh_from_db()
        self.assertEqual(book.title, 'Old Name')
        self.assertEqual(book.avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(catalog.source_mtime_ns, original_mtime)
        self.assertEqual(scanner.bad_archives, 1)

    def test_oversize_changed_inpx_keeps_existing_book_and_shelf(self):
        fields = ['Doe, Jane', 'fantasy', 'Old Name', '', '', '123', '100',
                  'id', '', 'fb2', '2020-01-01', 'en']
        with TemporaryDirectory() as root:
            target = Path(root, 'index.inpx')
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_INPX_ENABLE = True
            config.SOPDS_INPX_SKIP_UNCHANGED = True
            config.SOPDS_INPX_TEST_ZIP = False
            config.SOPDS_INPX_TEST_FILES = False
            with zipfile.ZipFile(target, 'w') as archive:
                archive.writestr('books.inp', '\x04'.join(fields).encode() + b'\n')
            opdsScanner().scan_all()
            book = Book.objects.get(filename='123.fb2')
            catalog = book.catalog.parent.parent
            original_mtime = catalog.source_mtime_ns
            user = User.objects.create_user('oversize_inpx', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            with zipfile.ZipFile(target, 'w') as archive:
                archive.writestr('books.inp', b'x' * 129 + b'\n')
            with patch('opds_catalog.inpx_parser.MAX_INP_LINE_BYTES', 128):
                scanner = opdsScanner()
                scanner.scan_all()

        book.refresh_from_db()
        catalog.refresh_from_db()
        self.assertEqual(book.title, 'Old Name')
        self.assertEqual(book.avail, 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertEqual(catalog.source_mtime_ns, original_mtime)
        self.assertEqual(scanner.bad_archives, 1)


class ScannerConcurrencyTests(TransactionTestCase):
    def test_interrupted_scan_keeps_old_books_until_successful_retry(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            old_file = Path(root, source.name)
            shutil.copyfile(source, old_file)
            command = ScannerCommand()
            command.logger = logging.getLogger('scan-interruption-test')
            command.scan()
            book = Book.objects.get(filename=source.name)
            user = User.objects.create_user('interrupted_scan', password='test-password')
            shelf = bookshelf.objects.create(user=user, book=book)

            old_file.unlink()
            shutil.copyfile(source, Path(root, 'new.fb2'))
            original_processfile = opdsScanner.processfile

            def process_then_interrupt(scanner, *args, **kwargs):
                original_processfile(scanner, *args, **kwargs)
                raise RuntimeError('interrupt after inserting the new book')

            with patch('opds_catalog.sopdscan.opdsScanner.processfile',
                       process_then_interrupt):
                with self.assertRaises(RuntimeError):
                    command.scan()

            book.refresh_from_db()
            new_book = Book.objects.get(filename='new.fb2')
            self.assertEqual(book.avail, 2)
            self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
            self.assertFalse(command.scan_is_active)

            command.scan()

        book.refresh_from_db()
        self.assertEqual(book.avail, 0)
        self.assertEqual(Book.objects.count(), 2)
        self.assertEqual(Book.objects.get(filename='new.fb2').pk, new_book.pk)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book.pk)
        self.assertFalse(command.scan_is_active)

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
