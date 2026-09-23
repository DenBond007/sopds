from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
import zipfile

from django.test import TestCase
from django.urls import reverse
from django.http import Http404

from constance import config
from opds_catalog import dl, opdsdb


class DownloadPathTests(TestCase):
    def test_regular_download_streams_bounded_chunks_and_closes_file(self):
        payload = b'x' * 150_000
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_AUTH = False
            Path(root, 'large.fb2').write_bytes(payload)
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'large.fb2', '.', catalog, 'fb2', 'Large', '', '', 'en', len(payload)
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))
            chunks = list(response.streaming_content)

        self.assertEqual(b''.join(chunks), payload)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 64 * 1024 for chunk in chunks))

    def test_downloads_file_inside_library(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_AUTH = False
            Path(root, 'inside.fb2').write_bytes(b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', '.', catalog, 'fb2', 'Inside', '', '', 'en', 14
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))
            content = (b''.join(response.streaming_content)
                       if response.streaming else response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content, b'synthetic book')
        self.assertTrue(response.streaming)

    def test_downloads_zip_member_inside_library(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_AUTH = False
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.writestr('inside.fb2', b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', 'books.zip', catalog, 'fb2', 'Inside', '', '',
                'en', 14, opdsdb.CAT_ZIP
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))
            content = (b''.join(response.streaming_content)
                       if response.streaming else response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content, b'synthetic book')
        self.assertTrue(response.streaming)

    def test_downloads_inpx_member_from_library_zip(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_AUTH = False
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.writestr('inside.fb2', b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', 'index.inpx/books.inp/books.zip', catalog,
                'fb2', 'Inside', '', '', 'en', 14, opdsdb.CAT_INP
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))
            content = (b''.join(response.streaming_content)
                       if response.streaming else response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content, b'synthetic book')
        self.assertTrue(response.streaming)

    def test_downloads_wrapped_zip_without_buffering_response(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_AUTH = False
            config.SOPDS_TITLE_AS_FILENAME = False
            Path(root, 'inside.fb2').write_bytes(b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', '.', catalog, 'fb2', 'Inside', '', '', 'en', 14
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 1}))
            content = (b''.join(response.streaming_content)
                       if response.streaming else response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        with zipfile.ZipFile(BytesIO(content)) as archive:
            self.assertEqual(archive.read('inside.fb2'), b'synthetic book')

    def test_wrapped_zip_removes_path_components_from_member_name(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_AUTH = False
            config.SOPDS_TITLE_AS_FILENAME = False
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.writestr('../outside.fb2', b'synthetic book')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                '../outside.fb2', 'books.zip', catalog, 'fb2', 'Outside',
                '', '', 'en', 14, opdsdb.CAT_ZIP
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 1}))
            content = b''.join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(BytesIO(content)) as archive:
            self.assertEqual(archive.namelist(), ['outside.fb2'])
            self.assertEqual(archive.read('outside.fb2'), b'synthetic book')

    def test_download_rejects_parent_path_outside_library(self):
        with TemporaryDirectory() as parent:
            library = Path(parent, 'library')
            library.mkdir()
            Path(parent, 'outside.fb2').write_bytes(b'synthetic outside data')
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_AUTH = False
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'outside.fb2', '..', catalog, 'fb2', 'Outside', '', '', 'en', 22
            )

            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))

        self.assertEqual(response.status_code, 404)

    def test_download_rejects_parent_filename_outside_library(self):
        with TemporaryDirectory() as parent:
            library = Path(parent, 'library')
            library.mkdir()
            Path(parent, 'outside.fb2').write_bytes(b'synthetic outside data')
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_AUTH = False
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                '../outside.fb2', '.', catalog, 'fb2', 'Outside', '', '', 'en', 22
            )
            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))

        self.assertEqual(response.status_code, 404)

    def test_download_rejects_symlink_outside_library(self):
        with TemporaryDirectory() as parent:
            library = Path(parent, 'library')
            library.mkdir()
            outside = Path(parent, 'outside.fb2')
            outside.write_bytes(b'synthetic outside data')
            (library / 'linked.fb2').symlink_to(outside)
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_AUTH = False
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'linked.fb2', '.', catalog, 'fb2', 'Linked', '', '', 'en', 22
            )

            response = self.client.get(reverse('opds_catalog:download',
                                               kwargs={'book_id': book.pk, 'zip_flag': 0}))

        self.assertEqual(response.status_code, 404)

    def test_other_readers_reject_parent_path_outside_library(self):
        with TemporaryDirectory() as parent:
            library = Path(parent, 'library')
            library.mkdir()
            Path(parent, 'outside.fb2').write_bytes(b'synthetic outside data')
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_AUTH = False
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'outside.fb2', '..', catalog, 'fb2', 'Outside', '', '', 'en', 22
            )

            for name, kwargs in (
                ('view_html', {'book_id': book.pk}),
                ('convert', {'book_id': book.pk, 'convert_type': 'epub'}),
                ('cover', {'book_id': book.pk}),
                ('thumb', {'book_id': book.pk}),
            ):
                with self.subTest(name=name):
                    response = self.client.get(reverse('opds_catalog:' + name,
                                                       kwargs=kwargs))
                    self.assertEqual(response.status_code, 404)
            with self.assertRaises(Http404):
                dl.getFileData(book)
            with self.assertRaises(Http404):
                dl.get_book_content(book)
