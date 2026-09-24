from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from constance import config
from opds_catalog import opdsdb
from opds_catalog.models import AnnotationTask, Book, Counter


class AnnotationPresentationTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = False
        catalog = opdsdb.addcattree('index.inpx/books.inp/books.zip')
        self.catalog = catalog
        self.book = opdsdb.addbook(
            '123.fb2', 'index.inpx/books.inp/books.zip', catalog, 'fb2',
            'Annotation Probe', 'NotYet', '', 'en', 100, opdsdb.CAT_INP,
        )

    @patch('zipfile.ZipFile', side_effect=AssertionError('archive opened in request'))
    def test_web_queues_once_and_open_page_can_read_finished_text(self, _zip):
        url = reverse('web:searchbooks')
        for _ in range(2):
            response = self.client.get(url, {'searchtype': 'm',
                                             'searchterms': 'Annotation Probe'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'NotYet', response.content)
            self.assertIn(f'data-annotation-book="{self.book.pk}"'.encode(),
                          response.content)
        self.assertEqual(AnnotationTask.objects.count(), 1)
        status_url = reverse('web:annotations')
        pending = self.client.get(status_url, {'ids': str(self.book.pk)})
        self.assertEqual(pending.json()['books'][str(self.book.pk)]['status'], 'pending')
        Book.objects.filter(pk=self.book.pk).update(annotation='Ready text')
        AnnotationTask.objects.filter(pk=self.book.pk).update(
            status='ready', last_checked_at=timezone.now())
        ready = self.client.get(status_url, {'ids': str(self.book.pk)})
        self.assertEqual(ready.json()['books'][str(self.book.pk)],
                         {'annotation': 'Ready text', 'status': 'ready'})

    @patch('zipfile.ZipFile', side_effect=AssertionError('archive opened in request'))
    def test_opds_uses_saved_text_on_next_request(self, _zip):
        url = reverse('opds_catalog:searchbooks', kwargs={
            'searchtype': 'm', 'searchterms': 'Annotation Probe'})
        first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertNotIn(b'NotYet', first.content)
        self.assertEqual(AnnotationTask.objects.count(), 1)
        Book.objects.filter(pk=self.book.pk).update(annotation='Ready text')
        AnnotationTask.objects.filter(pk=self.book.pk).update(
            status='ready', last_checked_at=timezone.now())
        second = self.client.get(url)
        self.assertEqual(second.status_code, 200)
        self.assertIn(b'Ready text', second.content)
        self.assertEqual(AnnotationTask.objects.count(), 1)

    @patch('zipfile.ZipFile', side_effect=AssertionError('archive opened in request'))
    def test_random_book_only_uses_saved_annotation(self, _zip):
        Counter.objects.update('allbooks', 1)
        response = self.client.get(reverse('web:main'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'NotYet', response.content)
        self.assertEqual(AnnotationTask.objects.count(), 0)

    @patch('zipfile.ZipFile', side_effect=AssertionError('archive opened in request'))
    def test_catalog_pages_queue_visible_book_without_opening_archive(self, _zip):
        web = self.client.get(reverse('web:catalog'), {'cat': self.catalog.pk})
        opds = self.client.get(reverse('opds_catalog:cat_tree', kwargs={
            'cat_id': self.catalog.pk}))
        self.assertEqual(web.status_code, 200)
        self.assertEqual(opds.status_code, 200)
        self.assertNotIn(b'NotYet', web.content)
        self.assertNotIn(b'NotYet', opds.content)
        self.assertEqual(AnnotationTask.objects.count(), 1)
