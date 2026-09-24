"""PostgreSQL-backed annotation worker. One process is sufficient."""

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from opds_catalog.dl import resolve_book_source_path
from opds_catalog.models import AnnotationTask, Book
from opds_catalog.opdsdb import CAT_INP


LEASE_SECONDS = 60
EXTRACTION_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3


def source_stat(book):
    path = resolve_book_source_path(book)
    stat = os.stat(path)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def claim_task():
    now = timezone.now()
    with transaction.atomic():
        task = (AnnotationTask.objects.select_for_update(skip_locked=True)
                .select_related('book')
                .filter(Q(status=AnnotationTask.STATUS_PENDING,
                          next_attempt_at__lte=now) |
                        Q(status=AnnotationTask.STATUS_RUNNING,
                          lease_until__lt=now))
                .order_by('next_attempt_at').first())
        if task is None:
            return None
        if task.attempts >= MAX_ATTEMPTS:
            task.status = AnnotationTask.STATUS_ERROR
            task.last_error = 'retry limit reached'
            task.last_checked_at = now
            task.lease_token = None
            task.lease_until = None
            task.save(update_fields=['status', 'last_error', 'last_checked_at',
                                     'lease_token', 'lease_until'])
            return None
        task.status = AnnotationTask.STATUS_RUNNING
        task.attempts += 1
        task.lease_token = uuid.uuid4()
        task.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        task.save(update_fields=['status', 'attempts', 'lease_token', 'lease_until'])
        return task


def run_extractor(book_id, previous_fingerprint):
    child_env = os.environ.copy()
    child_env['DB_NAME'] = connection.settings_dict['NAME']
    process = subprocess.Popen(
        [sys.executable, '-m', 'opds_catalog.annotation_extractor',
         str(book_id), previous_fingerprint],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True, env=child_env,
    )
    try:
        output, _ = process.communicate(timeout=EXTRACTION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise TimeoutError('annotation extraction timed out')
    if process.returncode or len(output) > 11000:
        raise RuntimeError('annotation extractor failed')
    result = json.loads(output)
    if result.get('status') == 'error':
        raise RuntimeError(str(result.get('error', 'extractor error'))[:160])
    if result.get('status') not in ('ready', 'missing', 'unchanged'):
        raise RuntimeError('invalid extractor result')
    return result


def process_one():
    task = claim_task()
    if task is None:
        return False
    book = task.book
    identity = (book.path, book.filename, book.cat_type, book.avail)
    try:
        if book.cat_type != CAT_INP or book.avail <= 0:
            raise ValueError('book is not an available INPX item')
        before = source_stat(book)
        result = run_extractor(book.pk, task.source_fingerprint)
        after = source_stat(book)
    except Exception as exc:
        result = {'status': 'error', 'error': type(exc).__name__}
        before = after = None
    with transaction.atomic():
        current = AnnotationTask.objects.select_for_update().filter(pk=task.pk).first()
        if current is None:
            return True
        if current.lease_token != task.lease_token or current.status != AnnotationTask.STATUS_RUNNING:
            return True
        current.lease_token = None
        current.lease_until = None
        locked_book = Book.objects.select_for_update().filter(pk=book.pk).first()
        if (locked_book is None or
                (locked_book.path, locked_book.filename, locked_book.cat_type,
                 locked_book.avail) != identity or
                (before is not None and before != after)):
            current.status = AnnotationTask.STATUS_PENDING
            current.attempts = 0
            current.next_attempt_at = timezone.now()
            current.last_error = 'source changed during extraction'
        elif result['status'] == 'error':
            current.last_error = result['error'][:160]
            if current.attempts < MAX_ATTEMPTS:
                current.status = AnnotationTask.STATUS_PENDING
                current.next_attempt_at = timezone.now() + timedelta(
                    seconds=30 * 2 ** (current.attempts - 1))
            else:
                current.status = AnnotationTask.STATUS_ERROR
                current.last_checked_at = timezone.now()
        else:
            current.status = (current.status if result['status'] == 'unchanged'
                              else result['status'])
            if current.status == AnnotationTask.STATUS_RUNNING:
                current.status = (AnnotationTask.STATUS_READY if locked_book.annotation
                                  else AnnotationTask.STATUS_MISSING)
            if result['status'] != 'unchanged':
                locked_book.annotation = result['annotation']
                locked_book.save(update_fields=['annotation'])
            current.source_fingerprint = result['fingerprint']
            current.last_checked_at = timezone.now()
            current.last_error = ''
        current.save()
    return True
