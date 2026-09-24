"""Bounded extraction run only inside the annotation worker's child process."""

import hashlib
import json
import os
import resource
import sys
import zipfile
from io import BytesIO

MAX_ANNOTATION_SOURCE_BYTES = 16 * 1024 * 1024
MAX_ANNOTATION_MEMORY_BYTES = 512 * 1024 * 1024


def extract(book_id, previous_fingerprint=''):
    from opds_catalog.dl import resolve_book_source_path
    from opds_catalog.models import Book, SIZE_BOOK_ANNOTATION
    from opds_catalog.opdsdb import CAT_INP
    from book_tools.format import create_bookfile

    book = Book.objects.get(pk=book_id, cat_type=CAT_INP, avail__gt=0)
    path = resolve_book_source_path(book)
    with open(path, 'rb') as source:
        before = os.fstat(source.fileno())
        with zipfile.ZipFile(source) as archive:
            info = archive.getinfo(book.filename)
            fingerprint = hashlib.sha256(json.dumps((
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                info.CRC, info.file_size, info.compress_size,
            )).encode()).hexdigest()
            if fingerprint == previous_fingerprint:
                return {'status': 'unchanged', 'fingerprint': fingerprint}
            if info.file_size > MAX_ANNOTATION_SOURCE_BYTES:
                raise ValueError('annotation source exceeds size limit')
            with archive.open(info) as member:
                data = member.read(MAX_ANNOTATION_SOURCE_BYTES + 1)
            if len(data) > MAX_ANNOTATION_SOURCE_BYTES:
                raise ValueError('annotation source exceeds size limit')
            parsed = create_bookfile(BytesIO(data), book.filename)
            value = parsed.description or ''
            if isinstance(value, bytes):
                value = value.decode('utf-8')
            value = value.strip(' \'&\n-.#\\`')[:SIZE_BOOK_ANNOTATION]
        after = os.fstat(source.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise RuntimeError('source changed during extraction')
    return {'status': 'ready' if value else 'missing',
            'annotation': value, 'fingerprint': fingerprint}


def main():
    resource.setrlimit(resource.RLIMIT_AS,
                       (MAX_ANNOTATION_MEMORY_BYTES, MAX_ANNOTATION_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    try:
        result = extract(int(sys.argv[1]), sys.argv[2])
    except Exception as exc:
        result = {'status': 'error', 'error': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sopds.settings')
    django.setup()
    main()
