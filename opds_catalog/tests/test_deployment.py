"""Data safety of the one-shot deployment lifecycle, using only Django's test DB."""
from io import StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from constance.backends.database.models import Constance
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TransactionTestCase
from opds_catalog import deployment
from opds_catalog.models import Book, Catalog, Genre, ReadingState, bookshelf

RELEASE = 'sha256:' + 'a' * 64
ENV = {'SOPDS_RELEASE': RELEASE, 'SOPDS_ADMIN_USERNAME': 'nas_admin',
       'SOPDS_ADMIN_EMAIL': 'admin@example.invalid', 'SOPDS_ADMIN_PASSWORD': 'Synthetic-test-only-6a!xB9'}


class DeploymentTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = self.settings(STATIC_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.environment = patch.dict(os.environ, ENV)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.drop_state()

    def tearDown(self):
        self.drop_state()

    def drop_state(self):
        with connection.cursor() as cursor:
            cursor.execute('DROP TABLE IF EXISTS sopds_bootstrap_state')

    def pending(self):
        # Django's runner has already migrated the test DB. The real empty-schema
        # decision/migrations are additionally exercised by the container smoke.
        with patch.object(connection.introspection, 'table_names', return_value=[]):
            self.assertTrue(deployment.prepare_state())

    def state(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT completed, ready_release FROM sopds_bootstrap_state WHERE id=1')
            return cursor.fetchone()

    def initialize(self):
        with patch.object(deployment, 'check_mounts'), patch.object(deployment, 'call_command') as command:
            # Keep real seeding (including the real fixture) and skip already-tested
            # migration/static machinery; its container integration runs separately.
            command.side_effect = lambda name, *args, **kw: call_command(name, *args, **kw) if name == 'loaddata' else None
            deployment.initialize(RELEASE)

    def test_initial_library_is_atomic_and_readiness_is_for_one_release(self):
        self.pending()
        self.initialize()
        user = get_user_model().objects.get()
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertTrue(user.check_password(ENV['SOPDS_ADMIN_PASSWORD']))
        self.assertGreater(Genre.objects.count(), 100)
        self.assertEqual(dict(Constance.objects.values_list('key', 'value')), deployment.DEFAULTS)
        self.assertEqual(self.state(), (True, RELEASE))
        deployment.check_ready()
        with patch.dict(os.environ, SOPDS_RELEASE='sha256:' + 'b' * 64):
            with self.assertRaises(CommandError):
                deployment.check_ready()

    def test_repeat_preserves_password_email_settings_custom_genres_and_user_data(self):
        self.pending()
        self.initialize()
        user = get_user_model().objects.get()
        user.set_password('Changed-after-install-8!xB7')
        user.email = 'changed@example.invalid'
        user.save()
        Constance.objects.filter(key='SOPDS_ROOT_LIB').update(value='/library/custom')
        Genre.objects.all().delete()
        genre = Genre.objects.create(genre='custom', section='Custom', subsection='Keep')
        catalog = Catalog.objects.create(cat_name='.', path='.')
        book = Book.objects.create(catalog=catalog, filename='synthetic.fb2', path='.',
            title='Keep', search_title='KEEP', format='fb2', annotation='Keep annotation', avail=2)
        shelf = bookshelf.objects.create(user=user, book=book)
        reading = ReadingState.objects.create(user=user, book=book, position=4321)
        before = user.password
        with patch.dict(os.environ, SOPDS_ADMIN_USERNAME='different', SOPDS_ADMIN_PASSWORD='invalid'):
            self.initialize()
        user.refresh_from_db()
        self.assertEqual((user.password, user.email), (before, 'changed@example.invalid'))
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(Constance.objects.get(key='SOPDS_ROOT_LIB').value, '/library/custom')
        self.assertEqual(list(Genre.objects.values_list('pk', flat=True)), [genre.pk])
        self.assertEqual(bookshelf.objects.get().pk, shelf.pk)
        self.assertEqual(ReadingState.objects.get(pk=reading.pk).position, 4321)
        self.assertEqual(Book.objects.get(pk=book.pk).annotation, 'Keep annotation')

    def test_interrupted_seed_rolls_back_all_seed_data_and_can_resume(self):
        self.pending()
        def fail_fixture(*args, **kwargs):
            Genre.objects.create(genre='interrupted', section='Probe', subsection='Probe')
            raise RuntimeError('Synthetic interrupted fixture')
        with patch.object(deployment, 'call_command', side_effect=fail_fixture):
            with self.assertRaises(RuntimeError):
                deployment.seed_new_library()
        self.assertFalse(Genre.objects.exists())
        self.assertFalse(Constance.objects.exists())
        self.assertFalse(get_user_model().objects.exists())
        self.assertEqual(self.state(), (False, ''))
        self.initialize()
        self.assertEqual(self.state(), (True, RELEASE))

    def test_failure_after_admin_commit_blocks_startup_then_resumes_without_reseeding(self):
        self.pending()
        real = deployment.call_command
        def command(name, *args, **kwargs):
            if name == 'loaddata':
                return real(name, *args, **kwargs)
            if name == 'collectstatic':
                raise OSError('Synthetic full static volume')
        with patch.object(deployment, 'check_mounts'), patch.object(deployment, 'call_command', side_effect=command):
            with self.assertRaises(OSError):
                deployment.initialize(RELEASE)
        password = get_user_model().objects.get().password
        self.assertEqual(self.state(), (True, ''))
        with self.assertRaises(CommandError):
            deployment.check_ready()
        self.initialize()
        self.assertEqual(get_user_model().objects.get().password, password)
        deployment.check_ready()

    def test_existing_database_without_checkpoint_gets_no_fixture_settings_or_admin(self):
        user = get_user_model().objects.create_user('existing', password='Existing-pass-8!')
        genre = Genre.objects.create(genre='existing', section='Keep', subsection='Keep')
        with patch.dict(os.environ, SOPDS_ADMIN_USERNAME='', SOPDS_ADMIN_PASSWORD=''):
            self.initialize()
        self.assertEqual(list(get_user_model().objects.values_list('pk', flat=True)), [user.pk])
        self.assertEqual(list(Genre.objects.values_list('pk', flat=True)), [genre.pk])
        self.assertFalse(Constance.objects.exists())
        deployment.check_ready()

    def test_pending_checkpoint_with_unexpected_data_refuses_overwrite(self):
        self.pending()
        genre = Genre.objects.create(genre='saved', section='Keep', subsection='Keep')
        with self.assertRaisesMessage(CommandError, 'refusing to overwrite'):
            deployment.seed_new_library()
        self.assertEqual(list(Genre.objects.values_list('pk', flat=True)), [genre.pk])
        self.assertEqual(self.state(), (False, ''))

    def test_migration_failure_and_bad_mount_invalidate_previous_readiness(self):
        self.pending()
        self.initialize()
        with patch.object(deployment, 'check_mounts', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                deployment.initialize(RELEASE)
        self.assertEqual(self.state(), (True, ''))
        self.assertFalse(Path(self.directory.name, '.sopds-ready').exists())
        with patch.object(deployment, 'check_mounts'), patch.object(deployment, 'call_command', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                deployment.initialize(RELEASE)
        with self.assertRaises(CommandError):
            deployment.check_ready()

    def test_invalid_initial_credentials_commit_no_checkpoint(self):
        with patch.dict(os.environ, SOPDS_ADMIN_PASSWORD='CHANGE_ME_BAD_PASSWORD'):
            with patch.object(connection.introspection, 'table_names', return_value=[]):
                with self.assertRaises(CommandError):
                    deployment.prepare_state()
        self.assertNotIn(deployment.STATE_TABLE, connection.introspection.table_names())

    def test_driver_error_is_redacted(self):
        from django.db import DatabaseError
        with patch.object(deployment, 'run_initialization', side_effect=DatabaseError('password=never-log-this')):
            with self.assertRaises(CommandError) as result:
                call_command('sopds_initialize', stdout=StringIO())
        self.assertNotIn('never-log-this', str(result.exception))

    def test_exclusive_initializer_lock_blocks_runtime_readiness(self):
        import psycopg2
        self.pending()
        self.initialize()
        database = connection.settings_dict
        with psycopg2.connect(dbname=database['NAME'], user=database['USER'], password=database['PASSWORD'],
                              host=database['HOST'], port=database['PORT']) as other:
            with other.cursor() as cursor:
                cursor.execute('SELECT pg_try_advisory_lock(%s, %s)', deployment.LOCK_KEYS)
                self.assertTrue(cursor.fetchone()[0])
                try:
                    with self.assertRaisesMessage(CommandError, 'still running'):
                        deployment.check_ready()
                finally:
                    cursor.execute('SELECT pg_advisory_unlock(%s, %s)', deployment.LOCK_KEYS)
        deployment.check_ready()
