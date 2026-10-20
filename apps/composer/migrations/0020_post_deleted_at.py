from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0019_merge_20260630_1149"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="deleted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
