from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from constance import config
import base64


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))


class SearchLinkTests(SimpleTestCase):
    def test_pagination_preserves_special_characters_on_all_search_pages(self):
        for template in ('sopds_books.html', 'sopds_authors.html', 'sopds_series.html'):
            with self.subTest(template=template):
                terms = 'War & Peace + 100% #fragment / русский'
                links = Links()
                links.feed(render_to_string(template, {
                    'searchtype': 'm', 'searchterms': terms,
                    'paginator': {'num_pages': 2, 'number': 1, 'has_next': True,
                                  'next_page_number': 2, 'page_range': [1, 2]},
                }))
                targets = [u for u in links.links if 'page=2' in u]
                self.assertTrue(targets)
                for target in targets:
                    self.assertEqual(parse_qs(urlsplit(target).query)['searchterms'], [terms])

    def test_duplicate_link_contains_real_book_id(self):
        links = Links()
        links.feed(render_to_string('sopds_books.html', {
            'books': [{'id': 42, 'title': 'Probe', 'format': 'fb2', 'filesize': 1, 'doubles': 1}],
        }))
        target = next(u for u in links.links if 'searchtype=d' in u)
        self.assertEqual(parse_qs(urlsplit(target).query)['searchterms'], ['42'])


class StatelessBasicTests(TestCase):
    def test_cookie_less_clients_do_not_create_sessions_or_update_last_login(self):
        config.SOPDS_AUTH = True
        user = get_user_model().objects.create_user('stateless_reader', password='test-password')
        header = 'Basic ' + base64.b64encode(b'stateless_reader:test-password').decode()
        before = Session.objects.count()
        for _ in range(3):
            response = Client().get('/opds/', HTTP_AUTHORIZATION=header)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('sessionid', response.cookies)
        self.assertEqual(Session.objects.count(), before)
        user.refresh_from_db()
        self.assertIsNone(user.last_login)

    def test_browser_session_still_authenticates_opds(self):
        config.SOPDS_AUTH = True
        user = get_user_model().objects.create_user('session_reader')
        self.client.force_login(user)
        self.assertEqual(self.client.get('/opds/').status_code, 200)
