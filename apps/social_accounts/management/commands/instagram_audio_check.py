"""Live check of the Instagram Audio API against a connected account.

The trending lookup cannot be verified without a real Facebook-Login
connection: the endpoint needs a user access token, and an app token is
refused (verified 2026-08-05, ``GET /v25.0/ig_audio`` with an app token
answers ``OAuthException code 190``). What is verifiable without an account
is that the path exists at all, because Graph answers a made-up path
differently (``GraphMethodException 100/33``) than a real one it cannot
authenticate.

So this command is the prepared test: run it the moment an Instagram
Business account is connected and it either prints real trending titles or
the exact reason Meta refused.

Usage:
    python manage.py instagram_audio_check
    python manage.py instagram_audio_check --query "walking shoes"
    python manage.py instagram_audio_check --account-id <uuid> --type original_sound
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Fetch trending (or searched) Instagram audio for a connected Instagram account."

    def add_arguments(self, parser):
        parser.add_argument(
            "--account-id",
            default=None,
            help="UUID of the Instagram SocialAccount. Defaults to the only connected one.",
        )
        parser.add_argument(
            "--query",
            default="",
            help="Search term. Leave empty for Meta's trending list.",
        )
        parser.add_argument(
            "--type",
            dest="audio_type",
            default="music",
            choices=["music", "original_sound"],
            help="Catalogue to read. Default: music.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=10,
            help="How many tracks to print. Default: 10.",
        )

    def handle(self, *args, **opts):
        from apps.analytics.tasks import _resolve_provider
        from apps.social_accounts.models import SocialAccount

        accounts = SocialAccount.objects.filter(platform="instagram")
        if opts["account_id"]:
            accounts = accounts.filter(id=opts["account_id"])
        connected = [a for a in accounts if a.connection_status == SocialAccount.ConnectionStatus.CONNECTED]

        if not connected:
            raise CommandError(
                "No connected Instagram account found. The Audio API needs a Facebook-Login "
                "connection with a linked Page; an app token is refused (OAuthException 190). "
                "Connect the account first, then run this command again."
            )
        if len(connected) > 1 and not opts["account_id"]:
            names = ", ".join(f"{a.account_name} ({a.id})" for a in connected)
            raise CommandError(f"Several connected Instagram accounts, pass --account-id: {names}")

        account = connected[0]
        provider = _resolve_provider(account)
        query = opts["query"].strip()

        what = f"search results for {query!r}" if query else "trending audio"
        self.stdout.write(f"Reading {what} for {account.account_name} ({account.account_platform_id})…")
        try:
            tracks = provider.list_audio(
                account.oauth_access_token,
                audio_type=opts["audio_type"],
                search_query=query,
                ig_user_id=account.account_platform_id,
            )
        except Exception as exc:
            raise CommandError(f"list_audio failed: {exc}") from exc

        if not tracks:
            self.stdout.write(
                self.style.WARNING(
                    "Meta returned no tracks. That is a valid answer: the catalogue open to third "
                    "parties is a subset of the app's, and business accounts see less of it."
                )
            )
            return

        for track in tracks[: opts["limit"]]:
            duration = f"{track['duration_ms'] / 1000:.0f}s" if track["duration_ms"] else "?"
            self.stdout.write(
                f"  {track['id']}  {track['title'] or '(untitled)'} - {track['artist'] or '?'} [{duration}]"
            )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{len(tracks)} track(s). Pass an id as audio_id to attach it to a reel."))
