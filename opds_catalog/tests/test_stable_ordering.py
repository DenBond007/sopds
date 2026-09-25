from xml.etree import ElementTree

from constance import config
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from opds_catalog.models import (
    Author, Book, Catalog, Genre, Series, bauthor, bgenre, bseries, bookshelf,
)


ATOM = '{http://www.w3.org/2005/Atom}'


class StableOrderingTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = False
        config.SOPDS_DOUBLES_HIDE = False
        config.SOPDS_MAXITEMS = 2
        self.catalog = Catalog.objects.create(cat_name='.', path='.')
        self.author = Author.objects.create(full_name='Probe', search_full_name='PROBE')
        self.genre = Genre.objects.create(genre='probe', section='Probe', subsection='Probe')
        self.series = Series.objects.create(ser='Probe', search_ser='PROBE')
        # Physical insertion order must differ from the public ID tie-breaker.
        self.ids = [101, 102, 103, 104, 105]
        for pk in (105, 101, 104, 102, 103):
            book = Book.objects.create(
                id=pk, catalog=self.catalog, filename=f'{pk}.fb2', path='.',
                format='fb2', title='Tied title', search_title='TIED TITLE',
                docdate='2020', annotation='Saved description', avail=2,
            )
            bauthor.objects.create(book=book, author=self.author)
            bgenre.objects.create(book=book, genre=self.genre)
            bseries.objects.create(book=book, ser=self.series, ser_no=3)

    def search_pages(self, surface, kind='books', searchtype='m', terms='Tied', **extra):
        result = []
        for page in range(1, 11):
            query = dict(searchtype=searchtype, searchterms=str(terms), page=page, **extra)
            if surface == 'web':
                response = self.client.get(reverse('web:search' + kind), query)
                self.assertEqual(response.status_code, 200)
                rows = response.context[kind]
                result.extend(row['id'] for row in rows)
                has_next = response.context['paginator']['has_next']
            else:
                response = self.client.get(reverse('opds_catalog:search' + kind, kwargs=query))
                self.assertEqual(response.status_code, 200)
                root = ElementTree.fromstring(response.content)
                result.extend(int(entry.find(ATOM + 'id').text.split(':')[1])
                              for entry in root.findall(ATOM + 'entry'))
                has_next = any(link.get('rel') == 'next' for link in root.findall(ATOM + 'link'))
            if not has_next:
                return result
        self.fail('Pagination failed to terminate')

    def test_book_search_ties_have_same_order_on_every_page_in_web_and_opds(self):
        for surface in ('web', 'opds'):
            for searchtype, terms in (
                ('m', 'Tied'), ('b', 'Tied'), ('a', self.author.pk),
                ('g', self.genre.pk), ('s', self.series.pk),
            ):
                with self.subTest(surface=surface, searchtype=searchtype):
                    self.assertEqual(self.search_pages(surface, searchtype=searchtype, terms=terms), self.ids)
        self.assertEqual(self.search_pages('opds', searchtype='e', terms='Tied title'), self.ids)
        self.assertEqual(self.search_pages('opds', searchtype='as', terms=self.author.pk,
                                          searchterms0=self.series.pk), self.ids)

    def test_join_duplicates_do_not_repeat_book_ids_or_change_saved_links(self):
        coauthor = Author.objects.create(full_name='Other', search_full_name='OTHER')
        for pk in self.ids:
            bauthor.objects.create(book_id=pk, author=self.author)
            bauthor.objects.create(book_id=pk, author=coauthor)
            bgenre.objects.create(book_id=pk, genre=self.genre)
            bseries.objects.create(book_id=pk, ser=self.series, ser_no=9)
        before = [list(model.objects.order_by('pk').values()) for model in (bauthor, bgenre, bseries)]
        for surface in ('web', 'opds'):
            for searchtype, terms in (('a', self.author.pk), ('g', self.genre.pk), ('s', self.series.pk)):
                with self.subTest(surface=surface, searchtype=searchtype):
                    self.assertEqual(self.search_pages(surface, searchtype=searchtype, terms=terms), self.ids)
            with self.subTest(surface=surface, searchtype='d'):
                self.assertEqual(self.search_pages(surface, searchtype='d', terms=self.ids[0]), self.ids[1:])
        self.assertEqual(self.search_pages('opds', searchtype='as', terms=self.author.pk,
                                          searchterms0=self.series.pk), self.ids)
        self.assertEqual([list(model.objects.order_by('pk').values())
                          for model in (bauthor, bgenre, bseries)], before)

    def test_series_order_uses_selected_series_only_and_keeps_number_priority(self):
        other = Series.objects.create(ser='Other', search_ser='OTHER')
        bseries.objects.filter(book_id=105, ser=self.series).update(ser_no=1)
        bseries.objects.create(book_id=104, ser=other, ser_no=0)
        expected = [105, 101, 102, 103, 104]
        for surface in ('web', 'opds'):
            with self.subTest(surface=surface):
                self.assertEqual(self.search_pages(surface, searchtype='s', terms=self.series.pk), expected)
        self.assertEqual(self.search_pages('opds', searchtype='as', terms=self.author.pk,
                                          searchterms0=self.series.pk), expected)

    def test_catalog_pages_preserve_tied_directories_then_tied_books(self):
        for pk in (205, 201, 203):
            Catalog.objects.create(id=pk, parent=self.catalog, cat_name='Same', path=f'./{pk}')
        expected = [('c', pk) for pk in (201, 203, 205)] + [('b', pk) for pk in self.ids]
        for surface in ('web', 'opds'):
            with self.subTest(surface=surface):
                result = []
                for page in range(1, 5):
                    if surface == 'web':
                        response = self.client.get(reverse('web:catalog'), {'cat': self.catalog.pk, 'page': page})
                        self.assertEqual(response.status_code, 200)
                        result.extend(('c' if row['is_catalog'] else 'b', row['id'])
                                      for row in response.context['items'])
                    else:
                        response = self.client.get(reverse('opds_catalog:cat_page', kwargs={
                            'cat_id': self.catalog.pk, 'page': page,
                        }))
                        self.assertEqual(response.status_code, 200)
                        root = ElementTree.fromstring(response.content)
                        for entry in root.findall(ATOM + 'entry'):
                            kind, pk = entry.find(ATOM + 'id').text.split(':')
                            result.append((kind, int(pk)))
                self.assertEqual(result, expected)

    def test_opds_author_without_series_excludes_books_in_series(self):
        bseries.objects.filter(book_id__in=(104, 105)).delete()
        self.assertEqual(self.search_pages('opds', searchtype='as', terms=self.author.pk,
                                          searchterms0=0), [104, 105])

    def test_author_and_series_search_pages_use_id_to_break_name_ties(self):
        for pk in (205, 201, 204, 202, 203):
            Author.objects.create(id=pk, full_name='Same', search_full_name='SAME')
            Series.objects.create(id=pk, ser='Same', search_ser='SAME')
        for surface in ('web', 'opds'):
            for kind in ('authors', 'series'):
                for searchtype in ('m', 'b', 'e'):
                    with self.subTest(surface=surface, kind=kind, searchtype=searchtype):
                        self.assertEqual(self.search_pages(surface, kind=kind,
                            searchtype=searchtype, terms='Same'), [201, 202, 203, 204, 205])

    def test_shelf_ties_keep_user_isolation_and_stable_order(self):
        config.SOPDS_AUTH = True
        user = get_user_model().objects.create_user('shelf_order')
        other = get_user_model().objects.create_user('shelf_other')
        now = timezone.now()
        for pk in (105, 101, 104, 102):
            bookshelf.objects.create(user=user, book_id=pk, readtime=now)
        bookshelf.objects.create(user=other, book_id=103, readtime=now)
        self.client.force_login(user)
        for surface in ('web', 'opds'):
            with self.subTest(surface=surface):
                self.assertEqual(self.search_pages(surface, searchtype='u', terms=0), [101, 102, 104, 105])

    def test_hidden_editions_keep_one_representative_and_existing_duplicate_count(self):
        config.SOPDS_DOUBLES_HIDE = True
        response = self.client.get(reverse('web:searchbooks'), {'searchtype': 'm', 'searchterms': 'Tied'})
        self.assertEqual([(row['id'], row['doubles']) for row in response.context['books']], [(101, 4)])
        for surface in ('web', 'opds'):
            with self.subTest(surface=surface):
                self.assertEqual(self.search_pages(surface), [101])
