from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ScanMetadataMigrationTests(TransactionTestCase):
    old = ('opds_catalog', '0001_initial')
    new = ('opds_catalog', '0006_bookshelf_user_book_unique')

    def test_existing_book_and_shelf_keep_ids(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.old])
        try:
            old_apps = executor.loader.project_state([self.old]).apps
            Catalog = old_apps.get_model('opds_catalog', 'Catalog')
            Book = old_apps.get_model('opds_catalog', 'Book')
            Shelf = old_apps.get_model('opds_catalog', 'bookshelf')
            Author = old_apps.get_model('opds_catalog', 'Author')
            Genre = old_apps.get_model('opds_catalog', 'Genre')
            Series = old_apps.get_model('opds_catalog', 'Series')
            BookAuthor = old_apps.get_model('opds_catalog', 'bauthor')
            BookGenre = old_apps.get_model('opds_catalog', 'bgenre')
            BookSeries = old_apps.get_model('opds_catalog', 'bseries')

            user = get_user_model().objects.create_user('migration_probe', password='test-password')
            catalog = Catalog.objects.create(cat_name='.', path='.', cat_type=0, cat_size=0)
            book = Book.objects.create(
                filename='migration-probe.fb2', path='.', catalog=catalog,
                format='fb2', docdate='', lang='en', title='Probe',
                search_title='PROBE', annotation='Legacy description', avail=2,
            )
            shelf = Shelf.objects.create(user_id=user.pk, book_id=book.pk)
            author = Author.objects.create(full_name='Legacy Author',
                                           search_full_name='LEGACY AUTHOR')
            genre = Genre.objects.create(genre='legacy', section='Legacy',
                                         subsection='Legacy genre')
            series = Series.objects.create(ser='Legacy series',
                                           search_ser='LEGACY SERIES')
            author_link = BookAuthor.objects.create(book_id=book.pk, author_id=author.pk)
            genre_link = BookGenre.objects.create(book_id=book.pk, genre_id=genre.pk)
            series_link = BookSeries.objects.create(book_id=book.pk, ser_id=series.pk,
                                                    ser_no=3)
            book_id, shelf_id = book.pk, shelf.pk
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate([self.new])

        new_apps = executor.loader.project_state([self.new]).apps
        NewBook = new_apps.get_model('opds_catalog', 'Book')
        NewCatalog = new_apps.get_model('opds_catalog', 'Catalog')
        NewShelf = new_apps.get_model('opds_catalog', 'bookshelf')
        NewBookAuthor = new_apps.get_model('opds_catalog', 'bauthor')
        NewBookGenre = new_apps.get_model('opds_catalog', 'bgenre')
        NewBookSeries = new_apps.get_model('opds_catalog', 'bseries')
        AnnotationTask = new_apps.get_model('opds_catalog', 'AnnotationTask')
        migrated = NewBook.objects.get(pk=book_id)
        self.assertIsNone(migrated.source_mtime_ns)
        self.assertIsNone(migrated.last_seen_scan_id)
        self.assertEqual(migrated.annotation, 'Legacy description')
        self.assertIsNone(NewCatalog.objects.get(pk=catalog.pk).source_mtime_ns)
        migrated_shelf = NewShelf.objects.get(pk=shelf_id)
        self.assertEqual((migrated_shelf.book_id, migrated_shelf.user_id,
                          migrated_shelf.readtime), (book_id, user.pk, shelf.readtime))
        self.assertTrue(get_user_model().objects.get(pk=user.pk).check_password('test-password'))
        self.assertEqual(NewBookAuthor.objects.get(pk=author_link.pk).author_id, author.pk)
        self.assertEqual(NewBookAuthor.objects.get(pk=author_link.pk).book_id, book_id)
        self.assertEqual(NewBookGenre.objects.get(pk=genre_link.pk).genre_id, genre.pk)
        self.assertEqual(NewBookGenre.objects.get(pk=genre_link.pk).book_id, book_id)
        migrated_series = NewBookSeries.objects.get(pk=series_link.pk)
        self.assertEqual((migrated_series.book_id, migrated_series.ser_id,
                          migrated_series.ser_no), (book_id, series.pk, 3))
        self.assertEqual(AnnotationTask.objects.count(), 0)
        task = AnnotationTask.objects.create(book_id=book_id)
        self.assertEqual(task.status, 'pending')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AnnotationTask.objects.create(book_id=book_id)


class BookshelfUniquenessMigrationTests(TransactionTestCase):
    old = ('opds_catalog', '0005_annotation_task')
    new = ('opds_catalog', '0006_bookshelf_user_book_unique')

    def test_duplicate_preflight_preserves_every_shelf_and_rolls_back_migration(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.old])
        apps = executor.loader.project_state([self.old]).apps
        Catalog = apps.get_model('opds_catalog', 'Catalog')
        Book = apps.get_model('opds_catalog', 'Book')
        Shelf = apps.get_model('opds_catalog', 'bookshelf')
        user = get_user_model().objects.create_user('duplicate_migration_probe')
        catalog = Catalog.objects.create(cat_name='.', path='.')
        book = Book.objects.create(
            filename='probe.fb2', path='.', catalog=catalog, format='fb2',
            title='Probe', search_title='PROBE', annotation='Saved annotation',
        )
        Shelf.objects.create(user_id=user.pk, book_id=book.pk)
        injected_duplicate = Shelf.objects.create(user_id=user.pk, book_id=book.pk)
        before = list(Shelf.objects.order_by('pk').values())
        try:
            with self.assertRaisesRegex(RuntimeError, 'Duplicate bookshelf entries'):
                MigrationExecutor(connection).migrate([self.new])
            self.assertEqual(list(Shelf.objects.order_by('pk').values()), before)
            self.assertEqual(Book.objects.get(pk=book.pk).annotation, 'Saved annotation')
            self.assertNotIn(self.new, MigrationExecutor(connection).loader.applied_migrations)
        finally:
            # Remove only the duplicate deliberately injected by this test,
            # then restore the current schema for the rest of the test suite.
            injected_duplicate.delete()
            MigrationExecutor(connection).migrate([self.new])
        self.assertEqual(list(Shelf.objects.values()), before[:1])
