from constance import config
from django.test import TestCase
from django.urls import reverse


class RequestValidationTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = False

    def test_invalid_web_parameters_return_client_error(self):
        cases = [('searchbooks', {'page': 'broken'}),
                 ('searchauthors', {'page': '-1'}),
                 ('searchseries', {'searchtype': 'unknown'}),
                 ('searchbooks', {'searchtype': 'unknown'}),
                 ('searchauthors', {'searchtype': 'unknown'}),
                 ('catalog', {'cat': 'broken'}),
                 ('catalog', {'page': 'broken'}),
                 ('book', {'lang': '99'}), ('author', {'lang': 'broken'}),
                 ('series', {'lang': '-1'}), ('genre', {'section': 'broken'}),
                 ('annotations', {'ids': '9' * 5000})]
        for name, query in cases:
            with self.subTest(name=name, query_length=len(str(query))):
                self.assertEqual(self.client.get(reverse('web:' + name), query).status_code, 400)

    def test_missing_books_and_genres_return_404(self):
        urls = ['/opds/download/2147483647/0/', '/opds/cover/2147483647/',
                '/opds/thumb/2147483647/', '/opds/thumb/',
                '/opds/view_html/2147483647/', '/opds/convert/2147483647/epub/',
                '/opds/search/books/d/2147483647/', '/opds/search/books/d/broken/',
                '/web/search/books/?searchtype=i&searchterms=2147483647',
                '/web/search/books/?searchtype=d&searchterms=2147483647',
                '/web/genre/?section=2147483647']
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
