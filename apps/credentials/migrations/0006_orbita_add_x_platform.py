"""Orbita-eigene Migration: X (Twitter) als Plattform ergaenzen.

Ersetzt die frühere ``0004_add_x_platform`` aus unserem Fork. Die hing
parallel zum Upstream-Strang (0004_rename_instagram_personal_to_login ->
0005_add_devto_platform) und erzeugte beim Upstream-Merge am 24.07.2026
zwei Blattknoten im Migrationsgraphen. Statt einer Merge-Migration hängt
unsere Änderung jetzt hinten an den Upstream-Strang an – damit bleibt der
Graph linear und der Endzustand deckt sich mit models.py.

Die Choice-Liste enthält deshalb sowohl DEV.to (Upstream) als auch X (wir).
An der Datenbank ändert das nichts: choices sind reine Django-Metadaten,
die Spalte bleibt CharField(max_length=30).
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


class Migration(migrations.Migration):
    dependencies = [
        ("credentials", "0005_add_devto_platform"),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformcredential",
            name="platform",
            field=models.CharField(choices=PLATFORM_CHOICES, max_length=30),
        ),
    ]
