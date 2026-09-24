from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from opds_catalog import opdsdb
from opds_catalog.annotations import enqueue_visible
from opds_catalog.models import AnnotationTask


class AnnotationQueueTests(TestCase):
    def make_book(self, archive):
        catalog = opdsdb.addcattree('index.inpx/books.inp/books.zip')
        return opdsdb.addbook('123.fb2', 'index.inpx/books.inp/books.zip',
                              catalog, 'fb2', 'Test', '', '', 'en', 100, archive)

    def test_visible_inpx_book_has_one_task_and_other_books_have_none(self):
        book = self.make_book(opdsdb.CAT_INP)
        regular = self.make_book(opdsdb.CAT_NORMAL)
        enqueue_visible([book, regular])
        enqueue_visible([book, book])
        self.assertEqual(list(AnnotationTask.objects.values_list('book_id', flat=True)),
                         [book.pk])
        self.assertEqual(book.annotation, '')

    def test_completed_task_rechecks_at_most_once_per_day(self):
        book = self.make_book(opdsdb.CAT_INP)
        task = AnnotationTask.objects.create(
            book=book, status=AnnotationTask.STATUS_READY,
            last_checked_at=timezone.now(), attempts=2,
        )
        enqueue_visible([book])
        task.refresh_from_db()
        self.assertEqual(task.status, AnnotationTask.STATUS_READY)
        AnnotationTask.objects.filter(pk=book.pk).update(
            last_checked_at=timezone.now() - timedelta(days=2))
        enqueue_visible([book])
        task.refresh_from_db()
        self.assertEqual(task.status, AnnotationTask.STATUS_PENDING)
        self.assertEqual(task.attempts, 0)
