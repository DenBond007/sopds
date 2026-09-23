from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from constance import config
from opds_catalog import opdsdb
from opds_catalog.models import Author


class WebQueryTests(TestCase):
    def test_author_search_does_not_count_books_per_author(self):
        config.SOPDS_AUTH = False
        config.SOPDS_MAXITEMS = 60
        authors = []
        for index in range(5):
            authors.append(Author.objects.create(
                full_name=f'Author {index}',
                search_full_name=f'AUTHOR {index}',
                lang_code=9,
            ))
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook('book.fb2', '.', catalog, 'fb2', 'Book',
                              '', '', 'en', 10)
        opdsdb.addbauthor(book, authors[0])

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse('web:searchauthors'), {
                'searchtype': 'b', 'searchterms': 'Author',
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item['book_count'] for item in response.context['authors']],
            [1, 0, 0, 0, 0],
        )
        book_count_queries = [
            query['sql'] for query in captured
            if 'COUNT(' in query['sql'].upper()
            and 'OPDS_CATALOG_BOOK' in query['sql'].upper()
        ]
        self.assertLessEqual(len(book_count_queries), 1)
