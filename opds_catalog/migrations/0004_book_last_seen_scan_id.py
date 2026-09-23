from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('opds_catalog', '0003_catalog_source_mtime_ns'),
    ]

    operations = [
        migrations.AddField(
            model_name='book',
            name='last_seen_scan_id',
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
