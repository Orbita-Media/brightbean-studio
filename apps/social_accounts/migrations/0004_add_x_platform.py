from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("social_accounts", "0003_add_instagram_personal_platform"),
    ]

    operations = [
        migrations.AlterField(
            model_name="socialaccount",
            name="platform",
            field=models.CharField(
                choices=[
                    ("facebook", "Facebook"),
                    ("instagram", "Instagram"),
                    ("instagram_personal", "Instagram (Personal)"),
                    ("linkedin_personal", "LinkedIn (Personal Profile)"),
                    ("linkedin_company", "LinkedIn (Company Page)"),
                    ("tiktok", "TikTok"),
                    ("youtube", "YouTube"),
                    ("pinterest", "Pinterest"),
                    ("threads", "Threads"),
                    ("bluesky", "Bluesky"),
                    ("google_business", "Google Business Profile"),
                    ("mastodon", "Mastodon"),
                    ("x", "X (Twitter)"),
                ],
                max_length=30,
            ),
        ),
    ]
