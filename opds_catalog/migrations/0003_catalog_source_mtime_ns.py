from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('opds_catalog', '0002_book_source_mtime_ns'),
    ]

    operations = [
        migrations.AddField(
            model_name='catalog',
            name='source_mtime_ns',
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
