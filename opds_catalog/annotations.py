"""Cheap, idempotent scheduling for annotations of visible INPX books."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from opds_catalog.models import AnnotationTask
from opds_catalog.opdsdb import CAT_INP


RECHECK_AFTER = timedelta(hours=24)


def enqueue_visible(books):
    ids = {book.pk for book in books if book.cat_type == CAT_INP and book.avail > 0}
    if not ids:
        return
    now = timezone.now()
    AnnotationTask.objects.bulk_create(
        [AnnotationTask(book_id=book_id) for book_id in ids],
        ignore_conflicts=True,
    )
    AnnotationTask.objects.filter(
        book_id__in=ids,
        status__in=(AnnotationTask.STATUS_READY, AnnotationTask.STATUS_MISSING,
                    AnnotationTask.STATUS_ERROR),
    ).filter(Q(last_checked_at__lt=now - RECHECK_AFTER) |
             Q(last_checked_at__isnull=True)).update(
        status=AnnotationTask.STATUS_PENDING, attempts=0,
             next_attempt_at=now, last_error='')
