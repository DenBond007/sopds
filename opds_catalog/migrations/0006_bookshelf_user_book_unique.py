from django.db import migrations, models


def check_existing_shelves(apps, schema_editor):
    shelf = apps.get_model('opds_catalog', 'bookshelf')
    duplicates = (
        shelf.objects.using(schema_editor.connection.alias)
        .values('user_id', 'book_id')
        .annotate(entries=models.Count('pk'))
        .filter(entries__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            'Duplicate bookshelf entries for (user, book). Migration stopped '
            'without changing shelf data. Review duplicates on a backup copy '
            'and agree how to preserve IDs and readtime before retrying. '
            'See docs/modernization/operations.md.'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('opds_catalog', '0005_annotation_task'),
    ]

    operations = [
        migrations.RunPython(check_existing_shelves, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='bookshelf',
            constraint=models.UniqueConstraint(
                fields=('user', 'book'), name='bookshelf_user_book_uniq',
            ),
        ),
    ]
