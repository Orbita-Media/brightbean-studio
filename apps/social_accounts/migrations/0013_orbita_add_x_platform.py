"""Orbita-eigene Migration: X (Twitter) als Plattform ergänzen.

Ersetzt die frühere ``0004_add_x_platform`` aus unserem Fork. Die hing
parallel zum Upstream-Strang (0004_platformvisibility bis
0012_add_devto_platform) und erzeugte beim Upstream-Merge am 24.07.2026
zwei Blattknoten im Migrationsgraphen. Statt einer Merge-Migration hängt
unsere Änderung jetzt hinten an den Upstream-Strang an – damit bleibt der
Graph linear und der Endzustand deckt sich mit models.py.

Die Choice-Liste enthält deshalb sowohl DEV.to (Upstream) als auch X (wir).
Sie wird auf dieselben drei Modelle angewandt wie im Upstream-Pendant
0012_add_devto_platform. An der Datenbank ändert das nichts: choices sind
reine Django-Metadaten, die Spalten bleiben CharField(max_length=30).

Zusätzlich werden die beiden Steuer-Zeilen für X nachgeseedet. Die
Seed-Migrationen 0005_seed_platform_visibility und
0010_seed_analytics_platform_config laufen über die Choice-Liste ihres
eigenen Zeitpunkts – X (und im Upstream ebenso DEV.to) kommt danach und
bekäme sonst gar keine Zeile. Fehlende PlatformVisibility ist unkritisch
(ohne Zeile gilt „sichtbar"), fehlende AnalyticsPlatformConfig dagegen
schliesst die Plattform still von den Analytics aus.
"""

from django.db import migrations, models

PLATFORM_CHOICES = [
    ("facebook", "Facebook"),
    ("instagram", "Instagram"),
    ("instagram_login", "Instagram (Direct)"),
    ("linkedin_personal", "LinkedIn (Personal Profile)"),
    ("linkedin_company", "LinkedIn (Company Page)"),
    ("tiktok", "TikTok"),
    ("youtube", "YouTube"),
    ("pinterest", "Pinterest"),
    ("threads", "Threads"),
    ("bluesky", "Bluesky"),
    ("google_business", "Google Business Profile"),
    ("mastodon", "Mastodon"),
    ("devto", "DEV.to"),
    ("x", "X (Twitter)"),
]


def seed_x_platform_rows(apps, schema_editor):
    """PlatformVisibility- und AnalyticsPlatformConfig-Zeile für X anlegen."""
    PlatformVisibility = apps.get_model("social_accounts", "PlatformVisibility")
    AnalyticsPlatformConfig = apps.get_model("social_accounts", "AnalyticsPlatformConfig")
    PlatformVisibility.objects.get_or_create(platform="x", defaults={"is_visible": True})
    AnalyticsPlatformConfig.objects.get_or_create(platform="x", defaults={"is_enabled": True})


def unseed_x_platform_rows(apps, schema_editor):
    apps.get_model("social_accounts", "PlatformVisibility").objects.filter(platform="x").delete()
    apps.get_model("social_accounts", "AnalyticsPlatformConfig").objects.filter(platform="x").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("social_accounts", "0012_add_devto_platform"),
    ]

    operations = [
        migrations.AlterField(
            model_name="socialaccount",
            name="platform",
            field=models.CharField(choices=PLATFORM_CHOICES, max_length=30),
        ),
        migrations.AlterField(
            model_name="platformvisibility",
            name="platform",
            field=models.CharField(choices=PLATFORM_CHOICES, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="analyticsplatformconfig",
            name="platform",
            field=models.CharField(choices=PLATFORM_CHOICES, max_length=30, unique=True),
        ),
        migrations.RunPython(seed_x_platform_rows, unseed_x_platform_rows),
    ]
