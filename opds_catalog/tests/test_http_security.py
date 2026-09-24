import base64
import re
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from constance import config
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from opds_catalog import opdsdb
from opds_catalog.models import Counter, bookshelf
from opds_catalog.feeds import AuthFeed


class HttpSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        config.SOPDS_AUTH = True
        self.root = TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        config.SOPDS_ROOT_LIB = self.root.name
        config.SOPDS_TEMP_DIR = self.root.name
        self.payload = b'synthetic private book'
        Path(self.root.name, 'probe.fb2').write_bytes(self.payload)
        self.book = opdsdb.addbook(
            'probe.fb2', '.', opdsdb.addcattree('.'), 'fb2',
            'Private Probe', '', '', 'en', len(self.payload))
        self.user = get_user_model().objects.create_user('reader', password='test-password')
        self.other = get_user_model().objects.create_user('other', password='test-password')

    def book_url(self, name, **kwargs):
        return reverse('opds_catalog:' + name, kwargs={'book_id': self.book.pk, **kwargs})

    def test_all_book_routes_require_auth_before_reading_or_converting(self):
        routes = [self.book_url('download', zip_flag=0),
                  self.book_url('download', zip_flag=1), self.book_url('view_html'),
                  self.book_url('convert', convert_type='epub'),
                  self.book_url('cover'), self.book_url('thumb')]
        with patch('opds_catalog.dl.resolve_book_source_path',
                   side_effect=AssertionError('unauthenticated file access')), \
             patch('opds_catalog.dl.getFileDataConv',
                   side_effect=AssertionError('unauthenticated converter')):
            for url in routes:
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 401)
                    self.assertIn('Basic', response['WWW-Authenticate'])
        self.assertFalse(bookshelf.objects.exists())

    def test_malformed_basic_auth_returns_401(self):
        for header in ('Basic !!!', 'Basic YQ==', 'Basic /w==', 'Basic a',
                       'Basic', 'Bearer abc'):
            with self.subTest(header=header):
                response = self.client.get('/opds/', HTTP_AUTHORIZATION=header)
                self.assertEqual(response.status_code, 401)

    def test_basic_auth_download_preserves_bytes_and_own_shelf(self):
        credentials = base64.b64encode(b'reader:test-password').decode('ascii')
        response = self.client.get(self.book_url('download', zip_flag=0),
                                   HTTP_AUTHORIZATION='Basic ' + credentials)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.payload)
        self.assertEqual(list(bookshelf.objects.values_list('user_id', 'book_id')),
                         [(self.user.pk, self.book.pk)])

    def test_session_can_convert_but_cached_cover_is_not_public(self):
        self.client.force_login(self.user)
        with patch('opds_catalog.dl.getFileDataConv', return_value=BytesIO(b'converted')):
            converted = self.client.get(self.book_url('convert', convert_type='epub'))
            self.assertEqual(converted.status_code, 200)
        self.assertEqual(self.client.get(self.book_url('cover')).status_code, 200)
        self.assertEqual(Client().get(self.book_url('cover')).status_code, 401)

    def test_shelf_get_and_post_without_csrf_do_not_delete(self):
        bookshelf.objects.create(user=self.user, book=self.book)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for name in ('bsdel', 'bsclear'):
            url = reverse('web:' + name)
            with self.subTest(name=name):
                self.assertEqual(client.get(url, {'book': self.book.pk}).status_code, 405)
                self.assertEqual(client.post(url, {'book': self.book.pk}).status_code, 403)
                self.assertTrue(bookshelf.objects.filter(user=self.user, book=self.book).exists())

    def test_shelf_post_with_csrf_only_changes_current_users_shelf(self):
        bookshelf.objects.create(user=self.user, book=self.book)
        bookshelf.objects.create(user=self.other, book=self.book)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        page = client.get(reverse('web:searchbooks'), {'searchtype': 'u'})
        self.assertContains(page, 'method="post" action="' + reverse('web:bsdel') + '"')
        token = client.cookies['csrftoken'].value
        for name in ('bsdel', 'bsclear'):
            bookshelf.objects.get_or_create(user=self.user, book=self.book)
            response = client.post(reverse('web:' + name), {
                'book': self.book.pk, 'csrfmiddlewaretoken': token})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(bookshelf.objects.filter(user=self.user).exists())
            self.assertTrue(bookshelf.objects.filter(user=self.other, book=self.book).exists())

    def test_login_redirect_stays_on_current_https_host(self):
        for target in ('https://outside.invalid/', '//outside.invalid/',
                       'http://testserver/web/', 'javascript:alert(1)'):
            with self.subTest(target=target):
                response = self.client.post(reverse('web:login') + '?next=' + target,
                    {'username': 'reader', 'password': 'test-password'}, secure=True)
                self.assertEqual(response['Location'], reverse('web:main'))
        response = self.client.post(reverse('web:login') + '?next=/web/book/',
            {'username': 'reader', 'password': 'test-password'}, secure=True)
        self.assertEqual(response['Location'], '/web/book/')

    def test_logout_requires_csrf_post(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.get(reverse('web:logout')).status_code, 405)
        self.assertIn('_auth_user_id', client.session)
        self.assertEqual(client.post(reverse('web:logout')).status_code, 403)
        page = client.get(reverse('web:main'))
        self.assertContains(page, 'method="post" action="' + reverse('web:logout') + '"')
        response = client.post(reverse('web:logout'),
            {'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', client.session)

    def test_admin_logout_form_matches_django_post_endpoint(self):
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        page = self.client.get(reverse('admin:index'))
        self.assertContains(page, 'method="post" action="' + reverse('admin:logout') + '"')

    def test_login_page_does_not_disclose_random_private_book(self):
        Counter.objects.update('allbooks', 1)
        page = self.client.get(reverse('web:login'))
        self.assertNotContains(page, self.book.title)

    def test_stale_counter_does_not_crash_page(self):
        config.SOPDS_AUTH = False
        Counter.objects.update('allbooks', 500)
        with patch('sopds_web_backend.views.randint', return_value=500):
            self.assertEqual(self.client.get(reverse('web:main')).status_code, 200)

    def test_search_pages_do_not_share_cached_body(self):
        config.SOPDS_AUTH = False
        query = {'searchtype': 'm', 'searchterms': self.book.title}
        self.client.get(reverse('web:searchbooks'), query)
        authors = self.client.get(reverse('web:searchauthors'), query)
        self.assertNotContains(authors, self.book_url('download', zip_flag=0))

    def test_shelf_forms_use_each_sessions_csrf_token(self):
        for user in (self.user, self.other):
            client = Client(enforce_csrf_checks=True)
            client.force_login(user)
            page = client.get(reverse('web:searchbooks'),
                {'searchtype': 'm', 'searchterms': self.book.title})
            form = re.search(r'<form method="post" action="/web/bs/delete/">(.*?)</form>',
                             page.content.decode(), re.S)
            self.assertIsNotNone(form)
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form[1])[1]
            response = client.post(reverse('web:bsdel'),
                {'book': self.book.pk, 'csrfmiddlewaretoken': token})
            self.assertEqual(response.status_code, 302)

    def test_feed_requests_do_not_share_request_state(self):
        config.SOPDS_AUTH = False
        factory = RequestFactory()

        class ReentrantFeed(AuthFeed):
            title = 'Request isolation'
            description = 'Request isolation'
            link = '/opds/'

            def get_object(self, request, nested=False):
                if not nested:
                    self(factory.get('/nested/'), nested=True)
                return self.request.path

            def items(self, obj):
                return [obj]

            def item_title(self, item):
                return item

            def item_description(self, item):
                return item

            def item_link(self, item):
                return item

        response = ReentrantFeed()(factory.get('/outer/'))
        self.assertContains(response, '/outer/')
        self.assertNotContains(response, '/nested/')

    def test_login_on_private_https_port_passes_csrf_and_sets_secure_cookies(self):
        with self.settings(ALLOWED_HOSTS=['192.168.88.44'],
                           SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
                           SECURE_SSL_REDIRECT=True, SESSION_COOKIE_SECURE=True,
                           CSRF_COOKIE_SECURE=True,
                           CSRF_TRUSTED_ORIGINS=['https://192.168.88.44']):
            client = Client(enforce_csrf_checks=True, HTTP_HOST='192.168.88.44:8443',
                            HTTP_X_FORWARDED_PROTO='https')
            self.assertEqual(client.get(reverse('web:login')).status_code, 200)
            self.assertTrue(client.cookies['csrftoken']['secure'])
            response = client.post(reverse('web:login'), {
                'username': 'reader', 'password': 'test-password',
                'csrfmiddlewaretoken': client.cookies['csrftoken'].value},
                HTTP_ORIGIN='https://192.168.88.44:8443')
            self.assertEqual(response.status_code, 302)
            self.assertTrue(client.cookies['sessionid']['secure'])
            self.assertEqual(client.session['_auth_user_id'], str(self.user.pk))
