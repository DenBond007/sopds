import io
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.dl import ConvertFB2, getFileDataConv


class ConverterSafetyTests(TestCase):
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
