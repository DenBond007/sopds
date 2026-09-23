import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.http import Http404
from django.test import RequestFactory, TestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.dl import Cover, Cover0


class CoverResourceTests(TestCase):
    def test_cover_reads_regular_fixture(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_NOCOVER_PATH = str(Path(root, 'missing.jpg'))
            Path(root, source.name).write_bytes(source.read_bytes())
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                source.name, '.', catalog, 'fb2', 'Fixture', '', '', 'en',
                source.stat().st_size
            )
            response = Cover(RequestFactory().get('/cover-regular-fixture/'), book.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'\xff\xd8'))

    def test_cover_reads_zip_fixture(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_NOCOVER_PATH = str(Path(root, 'missing.jpg'))
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.writestr(source.name, source.read_bytes())
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                source.name, 'books.zip', catalog, 'fb2', 'Fixture', '', '',
                'en', source.stat().st_size, opdsdb.CAT_ZIP
            )
            response = Cover(RequestFactory().get('/cover-zip-fixture/'), book.pk)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'\xff\xd8'))

    def test_legacy_parse_error_closes_regular_book_file(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            Path(root, 'inside.fb2').write_bytes(b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', '.', catalog, 'fb2', 'Inside', '', '', 'en', 14
            )
            opened = []
            real_open = open

            def track_open(*args, **kwargs):
                stream = real_open(*args, **kwargs)
                opened.append(stream)
                return stream

            with patch('opds_catalog.dl.codecs.open', side_effect=track_open), \
                 patch('opds_catalog.dl.open', side_effect=track_open, create=True), \
                 patch('opds_catalog.dl.fb2parse.fb2parser.parse', side_effect=ValueError):
                with self.assertRaises(ValueError):
                    Cover0(RequestFactory().get('/'), book.pk)

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def test_legacy_parse_error_closes_zip_file(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.writestr('inside.fb2', b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', 'books.zip', catalog, 'fb2', 'Inside', '', '',
                'en', 14, opdsdb.CAT_ZIP
            )
            opened = []
            real_open = open

            def track_open(*args, **kwargs):
                stream = real_open(*args, **kwargs)
                opened.append(stream)
                return stream

            with patch('opds_catalog.dl.codecs.open', side_effect=track_open), \
                 patch('opds_catalog.dl.open', side_effect=track_open, create=True), \
                 patch('opds_catalog.dl.fb2parse.fb2parser.parse', side_effect=ValueError):
                with self.assertRaises(ValueError):
                    Cover0(RequestFactory().get('/'), book.pk)

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def test_parse_error_closes_regular_book_file(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_NOCOVER_PATH = str(Path(root, 'missing.jpg'))
            Path(root, 'inside.fb2').write_bytes(b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', '.', catalog, 'fb2', 'Inside', '', '', 'en', 14
            )
            opened = []
            real_open = open

            def track_open(*args, **kwargs):
                stream = real_open(*args, **kwargs)
                opened.append(stream)
                return stream

            with patch('opds_catalog.dl.open', side_effect=track_open, create=True):
                with patch('opds_catalog.dl.create_bookfile', side_effect=ValueError):
                    with self.assertRaises(Http404):
                        Cover(RequestFactory().get('/cover-regular-error/'), book.pk)

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def test_parse_error_closes_zip_file(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_NOCOVER_PATH = str(Path(root, 'missing.jpg'))
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.writestr('inside.fb2', b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', 'books.zip', catalog, 'fb2', 'Inside', '', '',
                'en', 14, opdsdb.CAT_ZIP
            )
            opened = []
            real_open = open

            def track_open(*args, **kwargs):
                stream = real_open(*args, **kwargs)
                opened.append(stream)
                return stream

            with patch('opds_catalog.dl.open', side_effect=track_open, create=True):
                with patch('opds_catalog.dl.create_bookfile', side_effect=ValueError):
                    with self.assertRaises(Http404):
                        Cover(RequestFactory().get('/cover-zip-error/'), book.pk)

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)
