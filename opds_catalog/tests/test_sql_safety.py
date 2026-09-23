from django.test import TestCase

from constance import config
from opds_catalog import opdsdb
from opds_catalog.feeds import AuthorsFeed, BooksFeed, SeriesFeed


class PrefixQuerySafetyTests(TestCase):
    def setUp(self):
        catalog = opdsdb.addcattree('.')
        opdsdb.addbook('alpha.fb2', '.', catalog, 'fb2', 'Alpha', '', '', 'en')

    def test_quote_in_prefix_is_data(self):
        feed = BooksFeed()
        feed.lang_code = 0
        self.assertEqual(list(feed.items((4, "A'B"))), [])

    def test_normal_prefix_still_counts_books(self):
        feed = BooksFeed()
        feed.lang_code = 0
        rows = list(feed.items((2, 'A')))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, 'AL')
        self.assertEqual(rows[0].cnt, 1)

    def test_quote_in_all_opds_prefixes_is_data(self):
        for feed_class in (BooksFeed, AuthorsFeed, SeriesFeed):
            with self.subTest(feed=feed_class.__name__):
                feed = feed_class()
                feed.lang_code = 0
                self.assertEqual(list(feed.items((4, "A'B"))), [])

    def test_quote_in_web_prefixes_does_not_break_query(self):
        config.SOPDS_AUTH = False
        for path in ('/web/book/', '/web/author/', '/web/series/'):
            with self.subTest(path=path):
                response = self.client.get(path, {'chars': "A'B"})
                self.assertEqual(response.status_code, 200)
