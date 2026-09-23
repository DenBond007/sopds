from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from constance import config
from opds_catalog import opdsdb
from opds_catalog.feeds import SearchAuthorsFeed
from opds_catalog.models import Author


class WebQueryTests(TestCase):
    def test_catalog_fetches_authors_once_for_page(self):
        config.SOPDS_AUTH = False
        config.SOPDS_MAXITEMS = 60
        catalog = opdsdb.addcattree('.')
        author = opdsdb.addauthor('Page Author')
        for index in range(5):
            book = opdsdb.addbook(
                f'book-{index}.fb2', '.', catalog, 'fb2',
                f'Book {index}', '', '', 'en', 10,
            )
            opdsdb.addbauthor(book, author)

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse('web:catalog'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Page Author', count=5)
        author_queries = [
            query['sql'] for query in captured
            if 'OPDS_CATALOG_BAUTHOR' in query['sql'].upper()
            and query['sql'].lstrip().upper().startswith('SELECT')
        ]
        self.assertLessEqual(len(author_queries), 1)

    def test_opds_author_search_does_not_count_books_per_entry(self):
        config.SOPDS_AUTH = False
        config.SOPDS_MAXITEMS = 60
        authors = [Author.objects.create(
            full_name=f'Author {index}', search_full_name=f'AUTHOR {index}',
            lang_code=9,
        ) for index in range(5)]
        catalog = opdsdb.addcattree('.')
        book = opdsdb.addbook('book.fb2', '.', catalog, 'fb2', 'Book',
                              '', '', 'en', 10)
        opdsdb.addbauthor(book, authors[0])

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse('opds_catalog:searchauthors',
                                               kwargs={'searchtype': 'b',
                                                       'searchterms': 'Author'}))

        self.assertEqual(response.status_code, 200)
        book_count_queries = [
            query['sql'] for query in captured
            if 'COUNT(' in query['sql'].upper()
            and 'OPDS_CATALOG_BOOK' in query['sql'].upper()
        ]
        self.assertLessEqual(len(book_count_queries), 1)
        feed = SearchAuthorsFeed()
        rows = feed.get_object(None, searchterms='Author', searchtype='b')['authors']
        self.assertEqual([row['book_count'] for row in rows], [1, 0, 0, 0, 0])

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
