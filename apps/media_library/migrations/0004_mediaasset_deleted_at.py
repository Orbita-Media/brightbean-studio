from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media_library", "0003_pendingupload"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
