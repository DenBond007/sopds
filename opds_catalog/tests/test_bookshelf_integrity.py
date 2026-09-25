import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from constance import config
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.db.models.query import QuerySet
from django.test import Client, TransactionTestCase
from django.urls import reverse

from opds_catalog import opdsdb
from opds_catalog.models import bookshelf


class BookshelfIntegrityTests(TransactionTestCase):
    def setUp(self):
        config.SOPDS_AUTH = True
        self.root = TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        config.SOPDS_ROOT_LIB = self.root.name
        self.payload = b'synthetic concurrent download'
        Path(self.root.name, 'probe.fb2').write_bytes(self.payload)
        self.book = opdsdb.addbook(
            'probe.fb2', '.', opdsdb.addcattree('.'), 'fb2',
            'Concurrent Probe', 'Saved annotation', '', 'en', len(self.payload),
        )
        self.user = get_user_model().objects.create_user(
            'concurrent_reader', password='test-password',
        )
        self.url = reverse('opds_catalog:download', kwargs={
            'book_id': self.book.pk, 'zip_flag': 0,
        })
        credentials = base64.b64encode(b'concurrent_reader:test-password').decode('ascii')
        self.authorization = 'Basic ' + credentials

    def test_database_rejects_duplicate_pair_but_allows_other_readers(self):
        shelf = bookshelf.objects.create(user=self.user, book=self.book)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                bookshelf.objects.create(user=self.user, book=self.book)
        other = get_user_model().objects.create_user('another_reader')
        bookshelf.objects.create(user=other, book=self.book)
        self.assertEqual(bookshelf.objects.count(), 2)
        self.assertEqual(bookshelf.objects.get(pk=shelf.pk).readtime, shelf.readtime)

    def test_concurrent_downloads_and_retry_preserve_one_shelf_and_file(self):
        # Hold both inserts until both requests have found the shelf empty.
        # Only creation is synchronized; database uniqueness must resolve the race.
        barrier = Barrier(2)
        original_create = QuerySet.create

        def synchronized_create(queryset, **kwargs):
            if queryset.model is bookshelf:
                barrier.wait(timeout=15)
            return original_create(queryset, **kwargs)

        def download():
            try:
                response = Client().get(self.url, HTTP_AUTHORIZATION=self.authorization)
                try:
                    payload = b''.join(response.streaming_content)
                    return response.status_code, payload
                finally:
                    response.close()
            finally:
                connections.close_all()

        with patch.object(QuerySet, 'create', synchronized_create):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(download) for _ in range(2)]
                results = [future.result(timeout=30) for future in futures]
        self.assertEqual(results, [(200, self.payload), (200, self.payload)])
        self.assertEqual(bookshelf.objects.count(), 1)
        before = list(bookshelf.objects.values())
        self.assertEqual(download(), (200, self.payload))
        self.assertEqual(list(bookshelf.objects.values()), before)
        self.assertEqual(Path(self.root.name, 'probe.fb2').read_bytes(), self.payload)
