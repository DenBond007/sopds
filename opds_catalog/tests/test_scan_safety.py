import os
import logging
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.models import Book, Catalog, bookshelf
from opds_catalog.management.commands.sopds_scanner import Command as ScannerCommand
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

            opdsScanner().scan_all()

        self.assertEqual(Book.objects.count(), 1)
        self.assertEqual(Book.objects.get(filename=fixture.name).pk, book_id)
        self.assertEqual(Book.objects.get(pk=book_id).catalog_id, catalog_id)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).book_id, book_id)

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
