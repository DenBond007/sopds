from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET
from constance import config
from django.contrib.auth import get_user_model
from django.db import connection, transaction, IntegrityError
from django.http import HttpResponse
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from opds_catalog.models import Author, Book, Catalog, Genre, ReadingState, Series, bauthor, bgenre, bseries, bookshelf
from opds_catalog.book_queries import trailing_duplicate_count


class ReadingFeatureTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = True
        config.SOPDS_DOUBLES_HIDE = False
        config.SOPDS_MAXITEMS = 1
        self.user = get_user_model().objects.create_user('feature_reader')
        self.other = get_user_model().objects.create_user('other_feature_reader')
        self.client.force_login(self.user)
        catalog = Catalog.objects.create(cat_name='.', path='.')
        self.books = [Book.objects.create(catalog=catalog, filename=f'{i}.fb2', path='.', format='fb2',
            title=f'War & Peace {i}', search_title=f'WAR & PEACE {i}', lang='en', annotation='', avail=2) for i in range(3)]
        author = Author.objects.create(full_name='Leo Tolstoy', search_full_name='LEO TOLSTOY')
        self.genre = Genre.objects.create(genre='novel', section='Books', subsection='Novel')
        series = Series.objects.create(ser='Collected', search_ser='COLLECTED')
        for book in self.books[:2]:
            bauthor.objects.create(book=book, author=author)
            bgenre.objects.create(book=book, genre=self.genre)
            bseries.objects.create(book=book, ser=series)
        self.url = reverse('web:reading_state', args=[self.books[0].pk])

    def test_combined_filters_match_in_web_and_opds_and_keep_next_link(self):
        params = {'searchtype': 'm', 'searchterms': 'War & Peace', 'author': 'Tolstoy',
                  'genre': self.genre.pk, 'series': 'Collected', 'language': 'en', 'format': 'fb2'}
        page = self.client.get(reverse('web:searchbooks'), params)
        self.assertEqual(page.status_code, 200)
        self.assertEqual([b['id'] for b in page.context['books']], [self.books[0].pk])
        self.assertContains(page, 'author=Tolstoy')
        feed = self.client.get(reverse('opds_catalog:searchbooks', kwargs={
            'searchtype': 'm', 'searchterms': 'War & Peace'}), {k:v for k,v in params.items() if k not in ('searchtype', 'searchterms')})
        self.assertEqual(feed.status_code, 200)
        xml = ET.fromstring(feed.content)
        atom = '{http://www.w3.org/2005/Atom}'
        self.assertEqual(xml.find(atom + 'entry/' + atom + 'id').text, 'b:' + str(self.books[0].pk))
        next_url = next(link.get('href') for link in xml.findall(atom + 'link') if link.get('rel') == 'next')
        self.assertEqual(parse_qs(urlsplit(next_url).query)['author'], ['Tolstoy'])
        self.assertEqual(self.client.get(next_url).status_code, 200)

    def test_personal_status_filter_and_progress_are_isolated(self):
        self.assertEqual(self.client.post(self.url, {'status':'reading', 'position':4321}).status_code, 302)
        ReadingState.objects.create(user=self.other, book=self.books[1], status='reading', position=9999)
        page = self.client.get(reverse('web:searchbooks'), {'searchtype':'m', 'reading_status':'reading'})
        self.assertEqual([b['id'] for b in page.context['books']], [self.books[0].pk])
        self.assertEqual(self.client.get(self.url).json(), {'status':'reading', 'position':4321})
        self.assertEqual(ReadingState.objects.filter(user=self.other).get().position, 9999)
        self.assertFalse(bookshelf.objects.exists())

    def test_update_requires_csrf_and_post_and_valid_bounded_values(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {'status':'read'}).status_code, 403)
        self.assertFalse(ReadingState.objects.exists())
        for values in ({'status':'invalid'}, {'position':'-1'}, {'position':'10001'}, {'position':'nan'}, {}):
            with self.subTest(values=values):
                self.assertEqual(self.client.post(self.url, values).status_code, 400)
        self.assertFalse(ReadingState.objects.exists())
        self.assertEqual(self.client.put(self.url).status_code, 405)
        page = csrf_client.get(reverse('web:searchbooks'), {'searchtype':'m'})
        token = csrf_client.cookies['csrftoken'].value
        self.assertEqual(csrf_client.post(self.url, {'status':'want', 'csrfmiddlewaretoken':token}).status_code, 302)

    def test_completion_and_removal_preserve_legacy_shelf(self):
        shelf = bookshelf.objects.create(user=self.user, book=self.books[0])
        self.client.post(self.url, {'position':10000})
        self.assertEqual(ReadingState.objects.get().status, 'read')
        self.client.post(self.url, {'action':'remove', 'next':'https://outside.invalid/'})
        self.assertFalse(ReadingState.objects.exists())
        self.assertEqual(bookshelf.objects.get().pk, shelf.pk)

    def test_anonymous_cannot_read_or_write_personal_data(self):
        self.assertEqual(Client().get(self.url).status_code, 302)
        self.assertEqual(Client().post(self.url, {'position':10}).status_code, 302)

    def test_reader_is_private_and_document_cannot_execute_scripts(self):
        self.assertEqual(self.client.get(reverse('web:reader', args=[self.books[0].pk])).status_code, 200)
        self.assertEqual(Client().get(reverse('web:reader_document', args=[self.books[0].pk])).status_code, 302)
        with patch('sopds_web_backend.library.ViewHtml', return_value=HttpResponse('safe book')):
            response = self.client.get(reverse('web:reader_document', args=[self.books[0].pk]))
        self.assertIn('sandbox allow-same-origin;', response['Content-Security-Policy'])
        self.assertNotIn('allow-scripts', response['Content-Security-Policy'])
        self.assertIn("default-src 'none'", response['Content-Security-Policy'])
        self.assertIn('no-store', response['Cache-Control'])

    def test_database_rejects_invalid_position_and_duplicate_owner_book(self):
        ReadingState.objects.create(user=self.user, book=self.books[0])
        for fields in ({'book':self.books[0]}, {'book':self.books[1], 'position':10001}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                ReadingState.objects.create(user=self.user, **fields)

    def test_duplicate_neighbors_use_batched_queries(self):
        catalog = self.books[0].catalog
        Book.objects.bulk_create([Book(catalog=catalog, filename=f'dup{i}', path='.',
            format='fb2', title='Same', search_title='SAME', annotation='') for i in range(300)])
        with CaptureQueriesContext(connection) as queries:
            count = trailing_duplicate_count(Book.objects.filter(title='Same').order_by('pk'), 0, 'Same', set())
        self.assertEqual(count, 300)
        self.assertLessEqual(len(queries), 4)
