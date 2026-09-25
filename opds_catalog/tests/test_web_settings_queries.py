from datetime import timedelta

from constance import config
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from opds_catalog.models import Book, Catalog, bookshelf


class WebSettingsQueryTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = True
        config.SOPDS_DOUBLES_HIDE = False
        config.SOPDS_MAXITEMS = 60
        catalog = Catalog.objects.create(cat_name='.', path='.')
        self.books = Book.objects.bulk_create([
            Book(catalog=catalog, filename=f'probe-{n}.fb2', path='.', format='fb2',
                 title=f'Query Probe {n:02}', search_title=f'QUERY PROBE {n:02}',
                 annotation='Saved description', avail=2)
            for n in range(60)
        ])
        self.user = get_user_model().objects.create_user('query_reader')
        self.client.force_login(self.user)
        self.search_url = reverse('web:searchbooks')
        self.search_params = {'searchtype': 'm', 'searchterms': 'Query Probe'}

    def test_sidebar_uses_one_query_for_zero_one_or_eight_books(self):
        other = get_user_model().objects.create_user('other_query_reader')
        bookshelf.objects.create(user=other, book=self.books[-1])
        url = reverse('web:searchauthors')
        params = {'searchtype': 'm', 'searchterms': 'No author'}
        self.client.get(url, params)  # Initialize missing Constance defaults first.
        rows = []
        now = timezone.now()
        for size in (0, 1, 8):
            for index in range(len(rows), size):
                rows.append(bookshelf.objects.create(
                    user=self.user, book=self.books[index],
                    readtime=now + timedelta(minutes=index),
                ))
            before = list(bookshelf.objects.order_by('pk').values())
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get(url, params)
            selects = [q['sql'] for q in captured
                       if q['sql'].lstrip().upper().startswith('SELECT')]
            shelf_queries = [q for q in selects if '"opds_catalog_bookshelf"' in q
                             or 'FROM "opds_catalog_book"' in q]
            print(f'SQL_METRIC sidebar books={size} book_and_shelf_selects={len(shelf_queries)}')
            with self.subTest(size=size):
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(shelf_queries), 1)
                actual = [(item['id'], item['book_id'], item['title'], item['readtime'])
                          for item in response.context['bookshelf']]
                expected = [(row.pk, row.book_id, row.book.title, row.readtime)
                            for row in reversed(rows)]
                self.assertEqual(actual, expected)
                for row in rows:
                    self.assertContains(response, row.book.title)
                    self.assertContains(response, reverse('opds_catalog:thumb',
                                                        kwargs={'book_id': row.book_id}))
                self.assertNotContains(response, self.books[-1].title)
                self.assertEqual(list(bookshelf.objects.order_by('pk').values()), before)

    def test_settings_selects_do_not_grow_with_search_or_catalog_page_size(self):
        routes = [('search', self.search_url, self.search_params, 'books'),
                  ('catalog', reverse('web:catalog'), {}, 'items')]
        for auth_enabled in (True, False):
            config.SOPDS_AUTH = auth_enabled
            for route, url, params, context_key in routes:
                self.client.get(url, params)
                counts = []
                for size in (1, 60):
                    config.SOPDS_MAXITEMS = size
                    with CaptureQueriesContext(connection) as captured:
                        response = self.client.get(url, params)
                    count = sum(q['sql'].lstrip().upper().startswith('SELECT')
                                and '"constance_config"' in q['sql'] for q in captured)
                    counts.append(count)
                    print(f'SQL_METRIC {route} auth={auth_enabled} books={size} '
                          f'settings_selects={count} total_queries={len(captured)}')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual([row['id'] for row in response.context[context_key]],
                                     [book.pk for book in self.books[:size]])
                with self.subTest(route=route, auth=auth_enabled):
                    self.assertEqual(counts[0], counts[1])

    def test_next_request_sees_new_settings_and_requires_auth_when_enabled(self):
        self.client.logout()
        config.SOPDS_AUTH = False
        config.SOPDS_MAXITEMS = 1
        config.SOPDS_ALPHABET_MENU = False
        config.SOPDS_FB2TOEPUB = ''
        first = self.client.get(self.search_url, self.search_params)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.context['books']), 1)
        self.assertFalse(first.context['sopds_auth'])
        self.assertFalse(first.context['alphabet'])
        self.assertFalse(first.context['fb2toepub'])

        config.SOPDS_AUTH = True
        config.SOPDS_MAXITEMS = 60
        config.SOPDS_ALPHABET_MENU = True
        config.SOPDS_FB2TOEPUB = 'synthetic-converter-not-executed'
        denied = self.client.get(self.search_url, self.search_params)
        self.assertEqual(denied.status_code, 302)
        self.assertTrue(denied['Location'].startswith(reverse('web:login')))
        self.client.force_login(self.user)
        second = self.client.get(self.search_url, self.search_params)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.context['books']), 60)
        self.assertTrue(second.context['sopds_auth'])
        self.assertTrue(second.context['alphabet'])
        self.assertTrue(second.context['fb2toepub'])

        self.client.logout()
        config.SOPDS_AUTH = False
        self.assertEqual(self.client.get(self.search_url, self.search_params).status_code, 200)
