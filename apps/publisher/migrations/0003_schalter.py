from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("publisher", "0002_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Schalter",
            fields=[
                ("name", models.CharField(max_length=100, primary_key=True, serialize=False)),
                ("an", models.BooleanField(default=False)),
                ("umgeschaltet_am", models.DateTimeField(auto_now=True)),
                ("umgeschaltet_von", models.CharField(blank=True, default="", max_length=255)),
            ],
            options={
                "db_table": "publisher_schalter",
            },
        ),
    ]
