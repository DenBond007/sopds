from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from constance import config
from opds_catalog import opdsdb


class CandidateSmokeTests(TestCase):
    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_web_opds_admin_and_download_without_debug(self):
        self.assertFalse(settings.DEBUG)
        config.SOPDS_AUTH = False
        with TemporaryDirectory() as root:
            payload = b'synthetic candidate book'
            Path(root, 'probe.fb2').write_bytes(payload)
            config.SOPDS_ROOT_LIB = root
            catalog = opdsdb.addcattree('.')
            book = opdsdb.addbook('probe.fb2', '.', catalog, 'fb2',
                                  'Candidate Probe', 'Saved description',
                                  '', 'en', len(payload))
            genre = opdsdb.addgenre('candidate')
            opdsdb.addbgenre(book, genre)

            web = self.client.get(reverse('web:searchbooks'), {
                'searchtype': 'm', 'searchterms': 'Candidate Probe'})
            self.assertEqual(web.status_code, 200)
            self.assertContains(web, 'Candidate Probe')
            self.assertContains(web, 'Saved description')

            opds = self.client.get(reverse('opds_catalog:searchbooks', kwargs={
                'searchtype': 'm', 'searchterms': 'Candidate Probe'}))
            self.assertEqual(opds.status_code, 200)
            self.assertContains(opds, 'Candidate Probe')
            self.assertContains(opds, 'Saved description')

            downloaded = self.client.get(reverse('opds_catalog:download', kwargs={
                'book_id': book.pk, 'zip_flag': 0}))
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(b''.join(downloaded.streaming_content), payload)

            admin = get_user_model().objects.create_superuser(
                'candidate_admin', 'candidate@example.invalid', 'test-password')
            self.client.force_login(admin)
            index = self.client.get(reverse('admin:index'))
            self.assertEqual(index.status_code, 200)
            changelist = self.client.get(reverse('admin:opds_catalog_genre_changelist'))
            self.assertEqual(changelist.status_code, 200)
            self.assertContains(changelist, 'candidate')
