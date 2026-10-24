"""Double-post protection after an unclear answer to the publish call.

Two posts went out twice: Instagram 128a96a9 (22.10.2026, media_publish
answered 500 "An unexpected error has occurred" although the reel was live)
and Facebook f3d0b1be (15.10.2026, "The read operation timed out" on the
photo upload that had published). Both times the plain retry posted again.

These tests pin the fix: an attempt whose outcome is unknown is looked up on
the platform, a post found there is adopted instead of posted again, every
further attempt looks again first, and a platform that cannot be checked
fails the row for a human – no automatic retry.
"""

from datetime import timedelta
from unittest.mock import patch

import httpx

from apps.publisher.engine import MAX_RETRIES, OUTCOME_LOOKUP_SLACK, PublishEngine
from apps.publisher.models import PublishLog
from apps.publisher.test_stuck_publishing import _Base
from providers.exceptions import APIError, PublishLookupUnavailableError, PublishOutcomeUnknownError
from providers.types import PostType, PublishResult

CAPTION = "Dein Wecker heißt Katzenverhalten"


def _unknown():
    """The 22.10.2026 answer, as publish_commit turns it into an error."""
    cause = APIError(
        'Instagram API error 500: {"error":{"message":"An unexpected error has occurred. '
        'Please retry your request later.","type":"OAuthException","is_transient":true,"code":2}}',
        status_code=500,
        platform="Instagram",
    )
    try:
        raise PublishOutcomeUnknownError(
            "Instagram: Antwort auf die Veröffentlichung unklar", platform="Instagram"
        ) from cause
    except PublishOutcomeUnknownError as exc:
        return exc


class _FakeProvider:
    """Stands in for a provider; scripted answers for publish and lookup."""

    platform_name = "Instagram"
    supported_post_types = [PostType.IMAGE, PostType.TEXT]
    max_media_per_post = None

    def __init__(self, publish=(), find=()):
        self._publish = list(publish)
        self._find = list(find)
        self.publish_calls = []
        self.find_calls = []

    @staticmethod
    def _next(effects):
        effect = effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    def publish_post(self, access_token, content):
        self.publish_calls.append(content)
        return self._next(self._publish)

    def find_published_post(self, access_token, content, *, since, exclude_ids=frozenset()):
        self.find_calls.append({"since": since, "exclude_ids": set(exclude_ids), "text": content.text})
        return self._next(self._find)


class _OutcomeBase(_Base):
    def setUp(self):
        super().setUp()
        self.account.platform = "instagram"
        self.account.account_platform_id = "17841466348000992"
        self.account.save()
        self.post.caption = CAPTION
        self.post.save()
        for patcher in (
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
            patch("apps.publisher.engine.OUTCOME_LOOKUP_DELAY", 0),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _run(self, provider):
        with patch("apps.publisher.engine.get_provider", return_value=provider):
            PublishEngine().poll_and_publish()

    @staticmethod
    def _live(media_id="18112059887156840"):
        return PublishResult(
            platform_post_id=media_id,
            url=f"https://www.instagram.com/p/{media_id}/",
            extra={"adopted_existing_post": True},
        )


class FirstAttemptUnknownTest(_OutcomeBase):
    def test_post_found_right_after_the_error_is_adopted(self):
        """The 22.10.2026 case: 500 on media_publish, the reel is already live."""
        from apps.composer.models import PlatformPost

        pp = self._pp()
        provider = _FakeProvider(publish=[_unknown()], find=[self._live()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(pp.platform_post_id, "18112059887156840")
        self.assertEqual(len(provider.publish_calls), 1)
        self.assertIsNone(pp.outcome_unknown_since)
        self.assertEqual(pp.retry_count, 0)
        log = PublishLog.objects.get(platform_post=pp)
        self.assertIn("übernommen und nicht erneut gepostet", log.error_message)
        # The window opens at the attempt start, minus the clock slack.
        self.assertEqual(provider.find_calls[0]["since"], pp.publishing_started_at - OUTCOME_LOOKUP_SLACK)
        self.assertEqual(provider.find_calls[0]["text"], CAPTION)

    def test_nothing_found_schedules_a_retry_that_will_look_first(self):
        from apps.composer.models import PlatformPost

        pp = self._pp()
        provider = _FakeProvider(publish=[_unknown()], find=[None])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 1)
        self.assertEqual(pp.outcome_unknown_since, pp.publishing_started_at)
        self.assertIn("nicht eindeutig geantwortet", pp.publish_error)
        self.assertIn("erneut nachgesehen", pp.publish_error)
        log = PublishLog.objects.get(platform_post=pp)
        self.assertEqual(log.status_code, 500)
        self.assertIn("unklar", log.error_message)

    def test_platform_that_cannot_be_checked_fails_without_retry(self):
        from apps.composer.models import PlatformPost

        pp = self._pp()
        provider = _FakeProvider(
            publish=[_unknown()],
            find=[PublishLookupUnavailableError("Eine Instagram-Story hat keinen Text.")],
        )
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertTrue(pp.publish_error.startswith("Möglicherweise schon veröffentlicht – bitte auf Instagram prüfen"))
        self.assertIn("Eine Instagram-Story hat keinen Text.", pp.publish_error)
        self.assertEqual(pp.retry_count, 0)
        self.assertIsNone(pp.next_retry_at)
        self.assertIsNone(pp.outcome_unknown_since)

        # And nothing tries again on later ticks.
        self._run(provider)
        self.assertEqual(len(provider.publish_calls), 1)

    def test_failed_lookup_right_after_the_error_still_retries_with_a_check(self):
        """A network hiccup on the lookup is no proof either way; the next attempt looks again."""
        from apps.composer.models import PlatformPost

        pp = self._pp()
        provider = _FakeProvider(publish=[_unknown()], find=[httpx.ConnectError("graph down")])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertIsNotNone(pp.outcome_unknown_since)
        self.assertIn("ging nicht", pp.publish_error)
        self.assertIn("graph down", pp.publish_error)

    def test_last_attempt_with_unclear_answer_fails_for_a_human(self):
        from apps.composer.models import PlatformPost

        pp = self._pp(retry_count=MAX_RETRIES, next_retry_at=self._past())
        provider = _FakeProvider(publish=[_unknown()], find=[None])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertTrue(pp.publish_error.startswith("Möglicherweise schon veröffentlicht"))
        self.assertIn(f"{MAX_RETRIES + 1} Versuchen", pp.publish_error)

    def test_error_before_the_commit_keeps_the_plain_retry(self):
        """Instagram container creation failing is safe to repeat (no lookup, no flag)."""
        from apps.composer.models import PlatformPost

        pp = self._pp()
        container_error = APIError("Instagram API error 500: container", status_code=500, platform="Instagram")
        provider = _FakeProvider(publish=[container_error])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 1)
        self.assertIsNone(pp.outcome_unknown_since)
        self.assertEqual(provider.find_calls, [])

    def test_posts_of_other_rows_never_count_as_a_match(self):
        from apps.composer.models import Post

        other_post = Post.objects.create(workspace=self.workspace, caption=CAPTION)
        self._pp(
            post=other_post,
            status="published",
            platform_post_id="ig-other-row",
            published_at=self._past(),
        )
        self._pp()
        provider = _FakeProvider(publish=[_unknown()], find=[None])
        self._run(provider)

        self.assertIn("ig-other-row", provider.find_calls[0]["exclude_ids"])

    @staticmethod
    def _past():
        from django.utils import timezone

        return timezone.now() - timedelta(minutes=1)


class RetryAfterUnknownTest(_OutcomeBase):
    def _retry_row(self, **fields):
        from django.utils import timezone

        defaults = {
            "retry_count": 1,
            "next_retry_at": timezone.now() - timedelta(seconds=1),
            "outcome_unknown_since": timezone.now() - timedelta(minutes=2),
            "publish_error": "Instagram hat auf die Veröffentlichung nicht eindeutig geantwortet",
        }
        defaults.update(fields)
        return self._pp(**defaults)

    def test_retry_adopts_the_live_post_instead_of_posting_again(self):
        """What should have happened at 12:03:14 on 22.10.2026."""
        from apps.composer.models import PlatformPost

        pp = self._retry_row()
        since = pp.outcome_unknown_since
        provider = _FakeProvider(publish=[], find=[self._live()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(provider.publish_calls, [])
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(pp.platform_post_id, "18112059887156840")
        self.assertEqual(pp.publish_error, "")
        self.assertIsNone(pp.outcome_unknown_since)
        self.assertEqual(provider.find_calls[0]["since"], since - OUTCOME_LOOKUP_SLACK)
        self.assertIsNone(pp.dispatched_at)

    def test_retry_posts_when_the_platform_shows_nothing(self):
        from apps.composer.models import PlatformPost

        pp = self._retry_row()
        provider = _FakeProvider(
            publish=[PublishResult(platform_post_id="ig-new", url="https://www.instagram.com/p/x/")],
            find=[None],
        )
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(len(provider.publish_calls), 1)
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(pp.platform_post_id, "ig-new")
        self.assertIsNone(pp.outcome_unknown_since)

    def test_retry_looks_from_the_first_unclear_attempt_on(self):
        """A second unclear answer must not narrow the window to its own start."""
        pp = self._retry_row()
        first_since = pp.outcome_unknown_since
        provider = _FakeProvider(publish=[_unknown()], find=[None, None])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.outcome_unknown_since, first_since)
        self.assertEqual([c["since"] for c in provider.find_calls], [first_since - OUTCOME_LOOKUP_SLACK] * 2)
        self.assertEqual(pp.retry_count, 2)

    def test_check_that_cannot_run_does_not_post(self):
        from apps.composer.models import PlatformPost

        pp = self._retry_row()
        provider = _FakeProvider(publish=[], find=[PublishLookupUnavailableError("Facebook-Seite unbekannt.")])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(provider.publish_calls, [])
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertTrue(pp.publish_error.startswith("Möglicherweise schon veröffentlicht"))
        self.assertIn("Facebook-Seite unbekannt.", pp.publish_error)

    def test_check_that_fails_transiently_retries_later_without_posting(self):
        from apps.composer.models import PlatformPost

        pp = self._retry_row()
        provider = _FakeProvider(publish=[], find=[httpx.ReadTimeout("graph slow")])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(provider.publish_calls, [])
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 2)
        self.assertIsNotNone(pp.outcome_unknown_since)
        self.assertIn("graph slow", pp.publish_error)

    def test_series_ending_on_another_error_still_warns(self):
        """A refused last attempt does not prove the unclear earlier one stayed offline."""
        from apps.composer.models import PlatformPost

        pp = self._retry_row(retry_count=MAX_RETRIES)
        refused = APIError("Instagram API error 400: Media ID is not available", status_code=400)
        provider = _FakeProvider(publish=[refused], find=[None])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertTrue(pp.publish_error.startswith("Möglicherweise schon veröffentlicht"))
        self.assertIn("Media ID is not available", pp.publish_error)
        self.assertIsNone(pp.outcome_unknown_since)


class SweepWithUnclearEarlierAttemptTest(_OutcomeBase):
    def test_interrupted_before_dispatch_keeps_the_check(self):
        from django.utils import timezone

        from apps.composer.models import PlatformPost

        since = timezone.now() - timedelta(hours=1)
        pp = self._pp(
            status=PlatformPost.Status.PUBLISHING,
            retry_count=1,
            publishing_started_at=timezone.now() - timedelta(minutes=40),
            outcome_unknown_since=since,
        )
        self.assertEqual(PublishEngine()._settle_interrupted(pp.id), PlatformPost.Status.SCHEDULED)

        pp.refresh_from_db()
        self.assertEqual(pp.outcome_unknown_since, since)
        self.assertIn("nachgesehen", pp.publish_error)
        self.assertNotIn("es ist nichts erschienen", pp.publish_error)

    def test_interrupted_before_dispatch_exhausted_warns(self):
        from django.utils import timezone

        from apps.composer.models import PlatformPost

        pp = self._pp(
            status=PlatformPost.Status.PUBLISHING,
            retry_count=MAX_RETRIES,
            publishing_started_at=timezone.now() - timedelta(minutes=40),
            outcome_unknown_since=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(PublishEngine()._settle_interrupted(pp.id), PlatformPost.Status.FAILED)

        pp.refresh_from_db()
        self.assertTrue(pp.publish_error.startswith("Möglicherweise schon veröffentlicht"))
        self.assertNotIn("Nichts ist erschienen", pp.publish_error)
