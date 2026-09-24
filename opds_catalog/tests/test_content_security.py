import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from constance import config
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escapejs

from convert.fb2_to_html import render_inline, XLINK_NS
from opds_catalog import opdsdb
from opds_catalog.models import bookshelf


class ConverterLinkTests(SimpleTestCase):
    def test_active_links_are_removed_but_text_and_web_links_survive(self):
        for url in ('javascript:alert(1)', 'JaVaScRiPt:alert(1)',
                    'java\nscript:alert(1)', 'data:text/html,test', 'file:///tmp/book'):
            with self.subTest(url=url):
                element = ET.Element('a', {f'{{{XLINK_NS}}}href': url})
                element.text = 'Book link'
                self.assertEqual(render_inline(element, {}), 'Book link')
        element = ET.Element('a', {f'{{{XLINK_NS}}}href': 'https://example.invalid/book'})
        element.text = 'Book link'
        self.assertIn('href="https://example.invalid/book"', render_inline(element, {}))

    def test_images_only_use_embedded_book_data(self):
        element = ET.Element('image', {f'{{{XLINK_NS}}}href': 'https://outside.invalid/track'})
        self.assertNotIn('<img', render_inline(element, {}))


class ContentSecurityTests(TestCase):
    def setUp(self):
        config.SOPDS_AUTH = False
        self.title = "');alert(1);// <img src=x onerror=alert(2)>"
        self.book = opdsdb.addbook('probe.fb2', '.', opdsdb.addcattree('.'), 'fb2',
                                  self.title, 'Saved description', '', 'en', 20)

    def test_opds_html_content_escapes_metadata(self):
        urls = [reverse('opds_catalog:searchbooks', kwargs={
            'searchtype': 'm', 'searchterms': 'alert'}),
            reverse('opds_catalog:cat_tree', kwargs={'cat_id': self.book.catalog_id})]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                feed = ET.fromstring(response.content)
                contents = feed.findall('.//{http://www.w3.org/2005/Atom}content')
                html = ''.join(content.text or '' for content in contents)
                self.assertIn('&lt;img', html)
                self.assertNotIn('<img', html)

    def test_shelf_title_cannot_break_javascript_string(self):
        config.SOPDS_AUTH = True
        user = get_user_model().objects.create_user('html-reader')
        bookshelf.objects.create(user=user, book=self.book)
        self.client.force_login(user)
        page = self.client.get(reverse('web:searchbooks'), {'searchtype': 'u'})
        self.assertContains(page, str(escapejs(self.title)))

    def test_html_preview_is_sandboxed(self):
        with TemporaryDirectory() as root:
            config.SOPDS_ROOT_LIB = root
            config.SOPDS_TEMP_DIR = str(Path(root, 'new-temp'))
            Path(root, 'probe.fb2').write_text('''<?xml version="1.0"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><book-title>Probe</book-title></title-info></description>
<body><section><p>Safe text</p></section></body></FictionBook>''')
            response = self.client.get(reverse('opds_catalog:view_html',
                                               kwargs={'book_id': self.book.pk}))
            self.assertEqual(response.status_code, 200)
            content = b''.join(response.streaming_content)
            self.assertIn(b'Safe text', content)
            self.assertIn('sandbox;', response['Content-Security-Policy'])
            self.assertNotIn('allow-scripts', response['Content-Security-Policy'])
            self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
            self.assertEqual(list(Path(config.SOPDS_TEMP_DIR).iterdir()), [])
