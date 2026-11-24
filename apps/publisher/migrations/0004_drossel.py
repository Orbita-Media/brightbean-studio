from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("publisher", "0003_schalter"),
    ]

    operations = [
        migrations.CreateModel(
            name="Drossel",
            fields=[
                ("host", models.CharField(max_length=100, primary_key=True, serialize=False)),
                ("bis", models.DateTimeField()),
                ("hart", models.BooleanField(default=False)),
                ("grund", models.CharField(blank=True, default="", max_length=500)),
                ("gesetzt_am", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "publisher_drossel"},
        ),
    ]
