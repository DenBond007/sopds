"""Reproducible synthetic SQL benchmark; inserts always roll back."""
import json
from math import ceil
from statistics import median
from time import perf_counter
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from opds_catalog.book_queries import trailing_duplicate_count
from opds_catalog.models import Book, Catalog


class Command(BaseCommand):
    help = 'Benchmark only a new empty sopds_bench_* database; all synthetic rows roll back.'

    def add_arguments(self, parser):
        parser.add_argument('--confirm-disposable-db', required=True)
        parser.add_argument('--books', type=int, default=100000)
        parser.add_argument('--repeats', type=int, default=7)

    def handle(self, **options):
        name = connection.settings_dict['NAME']
        if not name.startswith('sopds_bench_') or options['confirm_disposable_db'] != name:
            raise CommandError('Confirm the exact isolated sopds_bench_* database')
        if Book.objects.exists() or Catalog.objects.exists():
            raise CommandError('Benchmark database must be empty')
        count, repeats = options['books'], options['repeats']
        if not 1000 <= count <= 1000000 or not 3 <= repeats <= 50:
            raise CommandError('Use 1000..1000000 books and 3..50 repeats')

        def measure(factory):
            timings = []
            for _ in range(repeats):
                start = perf_counter(); list(factory()); timings.append((perf_counter()-start)*1000)
            timings.sort()
            return {'median_ms': round(median(timings), 3), 'p95_ms': round(timings[ceil(len(timings)*.95)-1], 3)}

        with transaction.atomic():
            catalog = Catalog.objects.create(cat_name='synthetic-benchmark', path='.')
            first_id = None
            for start in range(0, count, 1000):
                rows = Book.objects.bulk_create([Book(catalog=catalog, filename=f'{i}.fb2', path='.',
                    format='fb2', title=f'Synthetic book {i:06}', search_title=f'SYNTHETIC BOOK {i:06}',
                    annotation='', lang='en', avail=2) for i in range(start,min(start+1000,count))])
                if first_id is None:
                    first_id = rows[0].pk
            with connection.cursor() as cursor:
                cursor.execute('ANALYZE opds_catalog_book')
            offset = count * 3 // 4
            legacy = lambda: Book.objects.order_by('pk')[offset:offset+1]
            indexed = lambda: Book.objects.filter(pk__gte=first_id+offset).order_by('pk')[:1]
            contains = lambda: Book.objects.filter(search_title__contains='BOOK 00099').order_by('search_title', 'pk')[:60]
            result = {'books': count, 'repeats': repeats,
                      'random_offset': measure(legacy), 'random_pk_range': measure(indexed),
                      'substring_search': measure(contains),
                      'plans': {'offset':json.loads(legacy().explain(format='json', analyze=True, buffers=True)),
                                'pk_range':json.loads(indexed().explain(format='json', analyze=True, buffers=True)),
                                'substring':json.loads(contains().explain(format='json', analyze=True, buffers=True))}}
            Book.objects.filter(pk__lt=first_id+300).update(title='Same', search_title='SAME')
            duplicates = Book.objects.filter(title='Same').order_by('pk')
            with CaptureQueriesContext(connection) as queries:
                for row in duplicates:
                    set(row.authors.values_list('pk', flat=True))
            result['duplicates_legacy_queries'] = len(queries)
            with CaptureQueriesContext(connection) as queries:
                result['duplicates_count'] = trailing_duplicate_count(duplicates, 0, 'Same', set())
            result['duplicates_batched_queries'] = len(queries)
            transaction.set_rollback(True)
        result['synthetic_rows_rolled_back'] = not Book.objects.exists() and not Catalog.objects.exists()
        self.stdout.write(json.dumps(result, indent=2))
