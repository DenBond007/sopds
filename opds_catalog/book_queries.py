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
