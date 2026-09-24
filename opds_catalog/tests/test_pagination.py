from django.test import SimpleTestCase

from opds_catalog.opds_paginator import Paginator


class PaginationTests(SimpleTestCase):
    def test_catalog_pages_include_every_directory_and_book_exactly_once(self):
        for directories, books in ((0, 0), (0, 120), (120, 0), (80, 100),
                                   (1, 120), (60, 61), (59, 1)):
            with self.subTest(directories=directories, books=books):
                first = ['directory-' + str(n) for n in range(directories)]
                second = ['book-' + str(n) for n in range(books)]
                first_page = Paginator(directories, books, maxitems=60)
                pages = []
                for n in range(1, first_page.num_pages + 1):
                    p = Paginator(directories, books, n, maxitems=60)
                    items = first[p.d1_first_pos:p.d1_last_pos + 1]
                    items += second[p.d2_first_pos:p.d2_last_pos + 1]
                    self.assertLessEqual(len(items), 60)
                    if first or second:
                        self.assertTrue(items)
                    pages.extend(items)
                self.assertEqual(pages, first + second)

    def test_page_bounds_and_exact_multiple(self):
        self.assertEqual(Paginator(120, 0).num_pages, 2)
        self.assertEqual(Paginator(0, 0).num_pages, 1)
        self.assertEqual(Paginator(0, 120, page_num=0).page_num, 1)
        self.assertEqual(Paginator(0, 120, page_num=999).page_num, 2)
