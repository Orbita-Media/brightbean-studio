from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0022_platformpost_outcome_unknown_since"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformpost",
            name="analytics_fehler_am",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="platformpost",
            name="analytics_fehler_zahl",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
