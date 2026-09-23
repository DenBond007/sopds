import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase

from opds_catalog.inpx_parser import Inpx


class InpxParserSafetyTests(TestCase):
    def test_callback_error_closes_inpx_archive_and_member(self):
        with TemporaryDirectory() as root:
            source = Path(root, 'index.inpx')
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('books.inp', b'book\n')
            opened_archives = []
            opened_members = []
            real_zipfile = zipfile.ZipFile
            real_open = real_zipfile.open

            def track_zipfile(*args, **kwargs):
                archive = real_zipfile(*args, **kwargs)
                opened_archives.append(archive)
                return archive

            def track_open(archive, *args, **kwargs):
                member = real_open(archive, *args, **kwargs)
                opened_members.append(member)
                return member

            parser = Inpx(str(source), lambda *args: (_ for _ in ()).throw(ValueError('stop')))
            parser.TEST_ZIP = False
            parser.TEST_FILES = False
            with patch('opds_catalog.inpx_parser.zipfile.ZipFile', side_effect=track_zipfile), \
                 patch.object(real_zipfile, 'open', track_open):
                with self.assertRaisesRegex(ValueError, 'stop'):
                    parser.parse()

        self.assertEqual(len(opened_archives), 1)
        self.assertIsNone(opened_archives[0].fp)
        self.assertEqual(len(opened_members), 1)
        self.assertTrue(opened_members[0].closed)

    def test_structure_info_has_size_limit(self):
        with TemporaryDirectory() as root:
            source = Path(root, 'index.inpx')
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('structure.info', b'x' * 65)
            parser = Inpx(str(source), lambda *args: None)
            with patch('opds_catalog.inpx_parser.MAX_STRUCTURE_BYTES', 64, create=True):
                with self.assertRaises(ValueError):
                    parser.parse()

    def test_inp_line_has_size_limit(self):
        with TemporaryDirectory() as root:
            source = Path(root, 'index.inpx')
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('books.inp', b'x' * 129 + b'\n')
            parser = Inpx(str(source), lambda *args: None)
            parser.TEST_ZIP = False
            parser.TEST_FILES = False
            with patch('opds_catalog.inpx_parser.MAX_INP_LINE_BYTES', 128, create=True):
                with self.assertRaises(ValueError):
                    parser.parse()
