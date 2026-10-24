from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0020_post_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformpost",
            name="publishing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="platformpost",
            name="dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
