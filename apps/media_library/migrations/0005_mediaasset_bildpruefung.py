from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media_library", "0004_mediaasset_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="bildpruefung",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="sha256_datei",
            field=models.CharField(blank=True, max_length=1024, null=True),
        ),
    ]
