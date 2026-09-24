import zipfile
from io import BytesIO
from subprocess import TimeoutExpired
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from constance import config
from opds_catalog import dl, opdsdb


class ViewHtmlTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = False

    def test_style_insertion_crosses_read_boundary(self):
        source = b'<html><head></head><body>book</body></html>'
        output = BytesIO()
        with patch('opds_catalog.dl.DOWNLOAD_CHUNK_SIZE', 3):
            dl._write_styled_html(BytesIO(source), output)
        self.assertEqual(
            output.getvalue(), source.replace(b'</head>', dl.HTML_HEAD_STYLE)
        )

    def test_converter_timeout_cleans_workdir(self):
        with TemporaryDirectory() as root:
            library = Path(root, 'library')
            library.mkdir()
            workdir = Path(root, 'tmp')
            workdir.mkdir()
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_TEMP_DIR = str(workdir)
            (library / 'inside.fb2').write_bytes(b'<FictionBook/>')
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                'inside.fb2', '.', catalog, 'fb2', 'Inside', '', '', 'en', 14
            )
            with patch('opds_catalog.dl.subprocess.Popen') as popen, \
                 patch('opds_catalog.dl.os.killpg') as killpg:
                process = popen.return_value
                process.pid = 12345
                process.communicate.side_effect = [
                    TimeoutExpired('fb2_to_html.py', 300), (b'', b'')
                ]
                response = self.client.get(reverse('opds_catalog:view_html',
                                                   kwargs={'book_id': book.pk}))

            self.assertEqual(response.status_code, 404)
            self.assertEqual(list(workdir.iterdir()), [])
            killpg.assert_called_once()

    def test_regular_fb2_uses_native_converter_and_cleans_temp_files(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            library = Path(root, 'library')
            library.mkdir()
            workdir = Path(root, 'tmp')
            workdir.mkdir()
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_TEMP_DIR = str(workdir)
            (library / source.name).write_bytes(source.read_bytes())
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                source.name, '.', catalog, 'fb2', 'Fixture', '', '', 'en',
                source.stat().st_size
            )

            response = self.client.get(reverse('opds_catalog:view_html',
                                               kwargs={'book_id': book.pk}))

            self.assertTrue(response.streaming)
            html = b''.join(response.streaming_content).decode('utf-8')
            self.assertIn('The Sanctuary Sparrow', html)
            self.assertIn('background-color: #1b1b1b', html)
            self.assertEqual(list(workdir.iterdir()), [])

    def test_zip_fb2_uses_native_converter_and_cleans_temp_files(self):
        source = Path(__file__).parent / 'data' / '262001.fb2'
        with TemporaryDirectory() as root:
            library = Path(root, 'library')
            library.mkdir()
            workdir = Path(root, 'tmp')
            workdir.mkdir()
            config.SOPDS_ROOT_LIB = str(library)
            config.SOPDS_TEMP_DIR = str(workdir)
            with zipfile.ZipFile(library / 'books.zip', 'w') as archive:
                archive.writestr(source.name, source.read_bytes())
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook(
                source.name, 'books.zip', catalog, 'fb2', 'Fixture', '', '',
                'en', source.stat().st_size, opdsdb.CAT_ZIP
            )

            response = self.client.get(reverse('opds_catalog:view_html',
                                               kwargs={'book_id': book.pk}))

            self.assertTrue(response.streaming)
            html = b''.join(response.streaming_content).decode('utf-8')
            self.assertIn('The Sanctuary Sparrow', html)
            self.assertIn('background-color: #1b1b1b', html)
            self.assertEqual(list(workdir.iterdir()), [])
