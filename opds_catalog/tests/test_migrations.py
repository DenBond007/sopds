from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ScanMetadataMigrationTests(TransactionTestCase):
    old = ('opds_catalog', '0001_initial')
    new = ('opds_catalog', '0005_annotation_task')

    def test_existing_book_and_shelf_keep_ids(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.old])
        try:
            old_apps = executor.loader.project_state([self.old]).apps
            Catalog = old_apps.get_model('opds_catalog', 'Catalog')
            Book = old_apps.get_model('opds_catalog', 'Book')
            Shelf = old_apps.get_model('opds_catalog', 'bookshelf')

            user = get_user_model().objects.create_user('migration_probe', password='test-password')
            catalog = Catalog.objects.create(cat_name='.', path='.', cat_type=0, cat_size=0)
            book = Book.objects.create(
                filename='migration-probe.fb2', path='.', catalog=catalog,
                format='fb2', docdate='', lang='en', title='Probe',
                search_title='PROBE', annotation='', avail=2,
            )
            shelf = Shelf.objects.create(user_id=user.pk, book_id=book.pk)
            book_id, shelf_id = book.pk, shelf.pk
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate([self.new])

        new_apps = executor.loader.project_state([self.new]).apps
        NewBook = new_apps.get_model('opds_catalog', 'Book')
        NewCatalog = new_apps.get_model('opds_catalog', 'Catalog')
        NewShelf = new_apps.get_model('opds_catalog', 'bookshelf')
        AnnotationTask = new_apps.get_model('opds_catalog', 'AnnotationTask')
        migrated = NewBook.objects.get(pk=book_id)
        self.assertIsNone(migrated.source_mtime_ns)
        self.assertIsNone(migrated.last_seen_scan_id)
        self.assertIsNone(NewCatalog.objects.get(pk=catalog.pk).source_mtime_ns)
        self.assertEqual(NewShelf.objects.get(pk=shelf_id).book_id, book_id)
        self.assertEqual(AnnotationTask.objects.count(), 0)
        task = AnnotationTask.objects.create(book_id=book_id)
        self.assertEqual(task.status, 'pending')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AnnotationTask.objects.create(book_id=book_id)
