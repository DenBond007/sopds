from django.db.models import Min

from opds_catalog.models import Book


def books_in_series(series_id, author_id=None):
    """Return each book once, ordered by its position in the selected series.

    Legacy links may contain several positions for the same book/series.
    Use their lowest position for display without changing the stored links.
    """
    books = Book.objects.filter(series=series_id)
    if author_id is not None:
        books = books.filter(authors=author_id)
    return books.annotate(series_position=Min('bseries__ser_no')).order_by(
        'series_position', 'search_title', '-docdate', 'pk',
    )


def apply_book_filters(books, params, user):
    """Combined, bounded filters used by both HTML and OPDS searches."""
    from django.core.exceptions import BadRequest
    from opds_catalog.models import ReadingState
    from opds_catalog.request_params import integer_parameter

    for key in ('author', 'series', 'language', 'format', 'reading_status'):
        if len(params.get(key, '')) > 128:
            raise BadRequest('Filter is too long')
    author = params.get('author', '').strip()
    series = params.get('series', '').strip()
    if author:
        books = books.filter(authors__search_full_name__contains=author.upper())
    if series:
        books = books.filter(series__search_ser__contains=series.upper())
    if params.get('genre'):
        books = books.filter(genres=integer_parameter(params['genre'], minimum=1))
    if params.get('language'):
        books = books.filter(lang__iexact=params['language'].strip())
    if params.get('format'):
        fmt = params['format'].lower()
        if fmt not in ('fb2', 'epub', 'mobi', 'pdf', 'djvu'):
            raise BadRequest('Unknown book format')
        books = books.filter(format__iexact=fmt)
    status = params.get('reading_status', '')
    if status:
        if status not in dict(ReadingState.STATUS_CHOICES) or not user.is_authenticated:
            raise BadRequest('Invalid personal reading filter')
        books = books.filter(reading_states__user=user, reading_states__status=status)
    return books.distinct() if author or series or params.get('genre') or status else books


def title_search(terms, match='contains'):
    return Book.objects.filter(**{'search_title__' + match: terms.upper()}).order_by(
        'search_title', '-docdate', 'pk')


def trailing_duplicate_count(books, start, title, author_ids):
    """Stream bounded batches instead of issuing two queries for every edition."""
    count = 0
    rows = books.all()[start:].only('pk', 'title').prefetch_related('authors').iterator(chunk_size=128)
    try:
        for book in rows:
            if book.title.upper() != title.upper() or {a.pk for a in book.authors.all()} != author_ids:
                break
            count += 1
    finally:
        rows.close()
    return count
