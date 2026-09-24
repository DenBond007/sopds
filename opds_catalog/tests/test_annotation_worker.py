import os
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import zipfile

from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from constance import config
from opds_catalog import opdsdb
from opds_catalog.annotation_worker import claim_task, process_one
from opds_catalog.annotation_extractor import extract
from opds_catalog.models import AnnotationTask, Book


class AnnotationWorkerTests(TestCase):
    def setUp(self):
        catalog = opdsdb.addcattree('index.inpx/books.inp/books.zip')
        self.book = opdsdb.addbook(
            '123.fb2', 'index.inpx/books.inp/books.zip', catalog, 'fb2',
            'Test', '', '', 'en', 100, opdsdb.CAT_INP,
        )
        self.task = AnnotationTask.objects.create(book=self.book)

    @patch('opds_catalog.annotation_worker.source_stat', return_value=(1, 2, 3, 4))
    @patch('opds_catalog.annotation_worker.run_extractor')
    def test_saves_annotation_and_reuses_same_fingerprint(self, extract, _stat):
        extract.return_value = {'status': 'ready', 'annotation': 'Saved text',
                                'fingerprint': 'fingerprint'}
        self.assertTrue(process_one())
        self.book.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.book.annotation, 'Saved text')
        self.assertEqual(self.task.status, AnnotationTask.STATUS_READY)
        self.assertEqual(self.task.source_fingerprint, 'fingerprint')
        self.assertFalse(process_one())
        extract.assert_called_once_with(self.book.pk, '')
        self.task.status = AnnotationTask.STATUS_PENDING
        self.task.next_attempt_at = timezone.now()
        self.task.save(update_fields=['status', 'next_attempt_at'])
        extract.return_value = {'status': 'unchanged', 'fingerprint': 'fingerprint'}
        self.assertTrue(process_one())
        self.book.refresh_from_db()
        self.assertEqual(self.book.annotation, 'Saved text')
        self.assertEqual(extract.call_args.args, (self.book.pk, 'fingerprint'))

    def test_expired_running_lease_is_reclaimed_after_restart(self):
        self.task.status = AnnotationTask.STATUS_RUNNING
        self.task.lease_until = timezone.now() - timedelta(seconds=1)
        self.task.save(update_fields=['status', 'lease_until'])
        claimed = claim_task()
        self.assertEqual(claimed.pk, self.book.pk)
        self.assertEqual(claimed.status, AnnotationTask.STATUS_RUNNING)
        self.assertIsNotNone(claimed.lease_token)
        self.assertEqual(claimed.attempts, 1)

    @patch('opds_catalog.annotation_worker.source_stat', return_value=(1, 2, 3, 4))
    @patch('opds_catalog.annotation_worker.run_extractor')
    def test_changed_book_does_not_accept_stale_result(self, extract, _stat):
        def changed(*_args):
            Book.objects.filter(pk=self.book.pk).update(filename='replacement.fb2')
            return {'status': 'ready', 'annotation': 'Stale', 'fingerprint': 'old'}
        extract.side_effect = changed
        self.assertTrue(process_one())
        self.book.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.book.annotation, '')
        self.assertEqual(self.task.status, AnnotationTask.STATUS_PENDING)
        self.assertEqual(self.task.attempts, 0)

    @patch('opds_catalog.annotation_worker.source_stat', side_effect=[(1, 2, 3, 4), (1, 2, 5, 6)])
    @patch('opds_catalog.annotation_worker.run_extractor', return_value={
        'status': 'ready', 'annotation': 'Stale', 'fingerprint': 'old'})
    def test_changed_source_does_not_accept_stale_result(self, _extract, _stat):
        self.assertTrue(process_one())
        self.book.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(self.book.annotation, '')
        self.assertEqual(self.task.status, AnnotationTask.STATUS_PENDING)

    @patch('opds_catalog.annotation_worker.source_stat', return_value=(1, 2, 3, 4))
    @patch('opds_catalog.annotation_worker.run_extractor', side_effect=ValueError('broken'))
    def test_errors_retry_with_pause_then_stop(self, _extract, _stat):
        for attempt in range(1, 4):
            self.assertTrue(process_one())
            self.task.refresh_from_db()
            self.assertEqual(self.task.attempts, attempt)
            self.assertEqual(self.task.status, (AnnotationTask.STATUS_PENDING
                             if attempt < 3 else AnnotationTask.STATUS_ERROR))
            if attempt < 3:
                self.assertGreater(self.task.next_attempt_at, timezone.now())
                self.task.next_attempt_at = timezone.now()
                self.task.save(update_fields=['next_attempt_at'])
        self.assertEqual(self.book.annotation, '')


class AnnotationExtractionIntegrationTests(TransactionTestCase):
    def test_separate_process_extracts_then_reuses_saved_annotation(self):
        source = Path(__file__).parent / 'data' / 'mirer.epub'
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            with zipfile.ZipFile(Path(root, 'books.zip'), 'w') as archive:
                archive.write(source, 'mirer.epub')
            catalog = opdsdb.addcattree('index.inpx/books.inp/books.zip')
            book = opdsdb.addbook(
                'mirer.epub', 'index.inpx/books.inp/books.zip', catalog,
                'epub', 'Test', '', '', 'ru', 100, opdsdb.CAT_INP,
            )
            task = AnnotationTask.objects.create(book=book)
            self.assertTrue(process_one())
            book.refresh_from_db()
            task.refresh_from_db()
            self.assertEqual(task.status, AnnotationTask.STATUS_READY, task.last_error)
            self.assertEqual(book.annotation, 'Собрание произведений. Том 2')
            self.assertEqual(task.status, AnnotationTask.STATUS_READY)
            archive_path = Path(root, 'books.zip')
            old_fingerprint = task.source_fingerprint
            archive_stat = archive_path.stat()
            os.utime(archive_path, ns=(archive_stat.st_atime_ns,
                                       archive_stat.st_mtime_ns + 1_000_000_000))
            refreshed = extract(book.pk, old_fingerprint)
            self.assertEqual(refreshed['status'], 'ready')
            self.assertNotEqual(refreshed['fingerprint'], old_fingerprint)
            self.assertTrue(task.source_fingerprint)
            AnnotationTask.objects.filter(pk=book.pk).update(
                status=AnnotationTask.STATUS_PENDING,
                next_attempt_at=timezone.now(),
            )
            self.assertTrue(process_one())
            book.refresh_from_db()
            task.refresh_from_db()
            self.assertEqual(book.annotation, 'Собрание произведений. Том 2')
            self.assertEqual(task.status, AnnotationTask.STATUS_READY)

    def test_corrupt_zip_does_not_remove_book(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            Path(root, 'books.zip').write_bytes(b'corrupt ZIP')
            catalog = opdsdb.addcattree('index.inpx/books.inp/books.zip')
            book = opdsdb.addbook(
                'bad.fb2', 'index.inpx/books.inp/books.zip', catalog,
                'fb2', 'Test', '', '', 'ru', 100, opdsdb.CAT_INP,
            )
            task = AnnotationTask.objects.create(book=book)
            self.assertTrue(process_one())
            book.refresh_from_db()
            task.refresh_from_db()
            self.assertEqual(book.avail, 2)
            self.assertEqual(book.annotation, '')
            self.assertEqual(task.status, AnnotationTask.STATUS_PENDING)
            self.assertEqual(task.attempts, 1)
            self.assertGreater(task.next_attempt_at, timezone.now())
