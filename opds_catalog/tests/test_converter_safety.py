import io
import shutil
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.dl import ConvertFB2, getFileDataConv


class ConverterSafetyTests(TestCase):
    def test_timeout_stops_converter_and_removes_its_temporary_files(self):
        with TemporaryDirectory() as parent:
            temporary = Path(parent, 'temporary')
            temporary.mkdir()
            converter = Path(parent, 'slow-converter')
            converter.write_text('#!/bin/sh\ncp "$1" "$2"\nsleep 2\n')
            converter.chmod(0o700)
            config.SOPDS_TEMP_DIR = str(temporary)
            config.SOPDS_FB2TOEPUB = str(converter)
            book = SimpleNamespace(format='fb2', filename='fixture.fb2')

            started = time.monotonic()
            with patch('opds_catalog.dl.getFileData', return_value=io.BytesIO(b'book')):
                with patch('opds_catalog.dl.CONVERTER_TIMEOUT_SECONDS', 0.1,
                           create=True):
                    result = getFileDataConv(book, 'epub')
            elapsed = time.monotonic() - started

            self.assertIsNone(result)
            self.assertLess(elapsed, 1.0)
            self.assertEqual(list(temporary.iterdir()), [])

    def test_failed_converter_does_not_serve_partial_output(self):
        with TemporaryDirectory() as parent:
            temporary = Path(parent, 'temporary')
            temporary.mkdir()
            converter = Path(parent, 'failed-converter')
            converter.write_text('#!/bin/sh\ncp "$1" "$2"\nexit 7\n')
            converter.chmod(0o700)
            config.SOPDS_TEMP_DIR = str(temporary)
            config.SOPDS_FB2TOEPUB = str(converter)
            book = SimpleNamespace(format='fb2', filename='fixture.fb2')

            with patch('opds_catalog.dl.getFileData', return_value=io.BytesIO(b'book')):
                result = getFileDataConv(book, 'epub')

            self.assertIsNone(result)
            self.assertEqual(list(temporary.iterdir()), [])

    def test_helper_preserves_file_outside_temporary_directory(self):
        with TemporaryDirectory() as parent:
            temporary = Path(parent, 'temporary')
            temporary.mkdir()
            outside = Path(parent, 'outside.fb2')
            outside.write_bytes(b'synthetic sentinel')
            outside_output = Path(parent, 'outside.epub')
            outside_output.write_bytes(b'synthetic output sentinel')
            converter = Path(parent, 'converter')
            converter.write_text('#!/bin/sh\ncp "$1" "$2"\n')
            converter.chmod(0o700)
            config.SOPDS_TEMP_DIR = str(temporary)
            config.SOPDS_FB2TOEPUB = str(converter)
            book = SimpleNamespace(format='fb2', filename='../outside.fb2')

            with patch('opds_catalog.dl.getFileData', return_value=io.BytesIO(b'book')):
                with patch('opds_catalog.dl.getFileName', return_value='../outside.fb2'):
                    result = getFileDataConv(book, 'epub')

            self.assertEqual(result.read(), b'book')
            self.assertEqual(outside.read_bytes(), b'synthetic sentinel')
            self.assertEqual(outside_output.read_bytes(), b'synthetic output sentinel')
            self.assertEqual(list(temporary.iterdir()), [])

    def test_http_zip_converter_preserves_file_outside_temporary_directory(self):
        with TemporaryDirectory() as parent:
            library = Path(parent, 'library')
            temporary = Path(parent, 'temporary')
            library.mkdir()
            temporary.mkdir()
            outside = Path(parent, 'outside.fb2')
            outside.write_bytes(b'synthetic sentinel')
            with zipfile.ZipFile(library / 'books.zip', 'w') as archive:
                archive.writestr('../outside.fb2', b'book payload')
            converter = Path(parent, 'converter')
            converter.write_text('#!/bin/sh\ncp "$1" "$2"\n')
            converter.chmod(0o700)
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_TEMP_DIR = str(temporary)
            config.SOPDS_FB2TOEPUB = str(converter)
            config.SOPDS_AUTH = False
            config.SOPDS_TITLE_AS_FILENAME = True
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                '../outside.fb2', 'books.zip', catalog, 'fb2', 'Fixture',
                '', '', 'en', 12, opdsdb.CAT_ZIP
            )

            response = ConvertFB2(RequestFactory().get('/'), book.pk, 'epub')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'book payload')
            self.assertEqual(outside.read_bytes(), b'synthetic sentinel')
            self.assertEqual(list(temporary.iterdir()), [])

    def test_converter_path_is_executed_literally(self):
        with TemporaryDirectory() as root:
            converter = Path(root) / 'converter$(printf wrong)'
            converter.write_text('#!/bin/sh\ncp "$1" "$2"\n')
            converter.chmod(0o700)
            config.SOPDS_TEMP_DIR = root
            config.SOPDS_FB2TOEPUB = str(converter)
            book = SimpleNamespace(format='fb2', filename='fixture.fb2')

            with patch('opds_catalog.dl.getFileData', return_value=io.BytesIO(b'book')):
                with patch('opds_catalog.dl.getFileName', return_value='fixture.fb2'):
                    result = getFileDataConv(book, 'epub')

            self.assertIsNotNone(result)
            self.assertEqual(result.read(), b'book')

    def test_http_converter_uses_literal_executable_path(self):
        fixture = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            converter = Path(root) / 'converter$(printf wrong)'
            converter.write_text('#!/bin/sh\ncp "$1" "$2"\n')
            converter.chmod(0o700)
            shutil.copyfile(fixture, Path(root) / fixture.name)
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_TEMP_DIR = root
            config.SOPDS_FB2TOEPUB = str(converter)
            config.SOPDS_AUTH = False
            config.SOPDS_TITLE_AS_FILENAME = False
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                fixture.name, '.', catalog, 'fb2', 'Fixture', '', '', 'en'
            )

            response = ConvertFB2(RequestFactory().get('/'), book.pk, 'epub')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, fixture.read_bytes())
