from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0021_platformpost_publish_attempt_stamps"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformpost",
            name="outcome_unknown_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
