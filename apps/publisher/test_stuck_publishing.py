"""Retry backoff and recovery of rows stuck in ``publishing`` (23.10.2026).

Two posts (Instagram + Facebook) hung in ``publishing`` for a day: the first
attempt failed while fetching the image from R2, the primary due-query took
the retry row again before its ``next_retry_at``, and the second attempt died
half-way without a log line. Nothing ever looked at a ``publishing`` row
again. These tests pin the fix: retries only after their backoff, a crashed
attempt is settled at once, a periodic sweep settles orphans, and an
automatic retry happens only when the platform was provably never called.
"""

from concurrent.futures import Future
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.publisher.engine import MAX_RETRIES, STUCK_PUBLISHING_AFTER, PublishEngine
from apps.publisher.models import PublishLog

SUCCESS = {"success": True, "platform_post_id": "fb-123", "status_code": 200, "response": {}}


class _InlineExecutor:
    """Runs submitted work in the calling thread.

    The engine publishes in worker threads, and a thread has its own database
    connection that cannot see the uncommitted data of a ``TestCase``.
    """

    def __init__(self, max_workers=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # mirror ThreadPoolExecutor
            future.set_exception(exc)
        return future


class _Base(TestCase):
    def setUp(self):
        inline = patch("apps.publisher.engine.ThreadPoolExecutor", _InlineExecutor)
        inline.start()
        self.addCleanup(inline.stop)

        from apps.composer.models import Post
        from apps.organizations.models import Organization
        from apps.social_accounts.models import SocialAccount
        from apps.workspaces.models import Workspace

        self.org = Organization.objects.create(name="Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="WS")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="page-1",
            account_name="Orbita Media Verlag",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.post = Post.objects.create(workspace=self.workspace, caption="Emotionale Erschöpfung …")

    def _pp(self, **fields):
        from apps.composer.models import PlatformPost

        defaults = {
            "post": self.post,
            "social_account": self.account,
            "status": PlatformPost.Status.SCHEDULED,
            "scheduled_at": timezone.now() - timedelta(minutes=5),
        }
        defaults.update(fields)
        return PlatformPost.objects.create(**defaults)

    @staticmethod
    def _age(pp, **fields):
        """Backdate fields a model save would overwrite (``updated_at``)."""
        from apps.composer.models import PlatformPost

        PlatformPost.all_objects.filter(pk=pp.pk).update(**fields)
        pp.refresh_from_db()


class RetryBackoffTest(_Base):
    def test_primary_path_leaves_retry_rows_alone(self):
        pp = self._pp(retry_count=1, next_retry_at=timezone.now() - timedelta(minutes=1))
        due_ids = [p.id for p in PublishEngine()._get_due_platform_posts()]
        self.assertNotIn(pp.id, due_ids)

    def test_retry_waits_for_next_retry_at(self):
        from apps.composer.models import PlatformPost

        pp = self._pp(retry_count=1, next_retry_at=timezone.now() + timedelta(seconds=50))
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=SUCCESS) as dispatch:
            PublishEngine().poll_and_publish()

        dispatch.assert_not_called()
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)

    def test_retry_runs_once_backoff_has_passed(self):
        from apps.composer.models import PlatformPost

        pp = self._pp(
            retry_count=1,
            next_retry_at=timezone.now() - timedelta(seconds=1),
            publish_error="Could not connect to the endpoint URL",
        )
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=SUCCESS) as dispatch:
            PublishEngine().poll_and_publish()

        self.assertEqual(dispatch.call_count, 1)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(pp.platform_post_id, "fb-123")
        self.assertEqual(pp.publish_error, "")
        self.post.refresh_from_db()
        self.assertEqual(self.post.published_at, pp.published_at)

    def test_retry_respects_a_later_reschedule(self):
        pp = self._pp(
            retry_count=1,
            next_retry_at=timezone.now() - timedelta(minutes=1),
            scheduled_at=timezone.now() + timedelta(hours=2),
        )
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=SUCCESS) as dispatch:
            PublishEngine().poll_and_publish()

        dispatch.assert_not_called()
        pp.refresh_from_db()
        self.assertEqual(pp.status, "scheduled")

    def test_failed_first_attempt_is_not_retried_on_the_next_tick(self):
        """The 23.10.2026 sequence: the R2 fetch fails, the next poll must wait."""
        from apps.composer.models import PlatformPost

        pp = self._pp()
        error = Exception('Could not connect to the endpoint URL: "https://r2.example/x.jpg"')
        engine = PublishEngine()
        with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=error) as dispatch:
            engine.poll_and_publish()
            engine.poll_and_publish()

        self.assertEqual(dispatch.call_count, 1)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 1)
        self.assertGreater(pp.next_retry_at, timezone.now())
        self.assertEqual(PublishLog.objects.filter(platform_post=pp).count(), 1)

    def test_permanent_failure_resets_the_retry_series(self):
        from apps.composer.models import PlatformPost

        pp = self._pp(status=PlatformPost.Status.PUBLISHING, retry_count=MAX_RETRIES)
        PublishEngine()._schedule_retry(pp, "boom")
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertEqual(pp.retry_count, 0)
        self.assertIsNone(pp.next_retry_at)


class ClaimAndDispatchStampTest(_Base):
    def test_claim_stamps_attempt_start_and_updated_at(self):
        from apps.composer.models import PlatformPost

        pp = self._pp(dispatched_at=timezone.now() - timedelta(days=1))
        self._age(pp, updated_at=timezone.now() - timedelta(days=1))
        before = timezone.now()
        PublishEngine._claim([pp])

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHING)
        self.assertGreaterEqual(pp.publishing_started_at, before)
        self.assertGreaterEqual(pp.updated_at, before)
        self.assertIsNone(pp.dispatched_at)

    def test_dispatch_is_stamped_before_the_provider_is_called(self):
        from apps.publisher.tests import _build_dispatch_mocks

        engine, platform_post, provider = _build_dispatch_mocks("facebook", "page-1")
        order = []
        result = provider.publish_post.return_value

        def publish(*args, **kwargs):
            order.append("provider")
            return result

        provider.publish_post.side_effect = publish
        with (
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
            patch("apps.publisher.engine.get_provider", return_value=provider),
        ):
            engine._dispatch_to_provider(platform_post, on_dispatch=lambda: order.append("stamp"))
        self.assertEqual(order, ["stamp", "provider"])

    def test_no_stamp_when_the_attempt_fails_before_dispatch(self):
        from apps.publisher.tests import _build_dispatch_mocks

        engine, platform_post, provider = _build_dispatch_mocks("facebook", "page-1")
        stamps = []
        with (
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
            patch("apps.publisher.engine.get_provider", return_value=provider),
            patch.object(PublishEngine, "_block_on_dropped_media", side_effect=RuntimeError("media")),
            self.assertRaises(RuntimeError),
        ):
            engine._dispatch_to_provider(platform_post, on_dispatch=lambda: stamps.append(1))
        self.assertEqual(stamps, [])
        provider.publish_post.assert_not_called()

    def test_publish_attempt_persists_the_dispatch_stamp(self):
        pp = self._pp()

        def fake_dispatch(platform_post, on_dispatch=None):
            on_dispatch()
            return SUCCESS

        with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=fake_dispatch):
            PublishEngine().poll_and_publish()
        pp.refresh_from_db()
        self.assertEqual(pp.status, "published")
        self.assertIsNotNone(pp.dispatched_at)
        self.assertIsNotNone(pp.publishing_started_at)


class CrashedAttemptTest(_Base):
    """An attempt that dies half-way is settled right away, never left hanging."""

    def test_crash_before_dispatch_is_requeued(self):
        from apps.composer.models import PlatformPost

        pp = self._pp()
        with (
            patch.object(PublishEngine, "_dispatch_to_provider", side_effect=Exception("R2 down")),
            patch.object(PublishEngine, "_schedule_retry", side_effect=RuntimeError("db write failed")),
        ):
            PublishEngine().poll_and_publish()

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 1)
        self.assertIn("vor der Übergabe", pp.publish_error)
        self.assertIn("db write failed", pp.publish_error)

    def test_crash_after_dispatch_fails_for_a_human(self):
        from apps.composer.models import PlatformPost

        pp = self._pp()

        def dispatched_then_error(platform_post, on_dispatch=None):
            on_dispatch()
            raise Exception("read timed out")

        with (
            patch.object(PublishEngine, "_dispatch_to_provider", side_effect=dispatched_then_error),
            patch.object(PublishEngine, "_schedule_retry", side_effect=RuntimeError("db write failed")),
        ):
            PublishEngine().poll_and_publish()

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("unklar", pp.publish_error)
        self.assertEqual(pp.retry_count, 0)

    def test_failed_save_after_success_never_publishes_twice(self):
        from apps.composer.models import PlatformPost

        pp = self._pp()

        def dispatched_ok(platform_post, on_dispatch=None):
            on_dispatch()
            return SUCCESS

        with (
            patch.object(PublishEngine, "_dispatch_to_provider", side_effect=dispatched_ok) as dispatch,
            patch.object(PublishEngine, "_record_success", side_effect=RuntimeError("db write failed")),
        ):
            PublishEngine().poll_and_publish()
            PublishEngine().poll_and_publish()

        self.assertEqual(dispatch.call_count, 1)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("fb-123", pp.publish_error)


class StuckPublishingSweepTest(_Base):
    def _stuck(self, **fields):
        from apps.composer.models import PlatformPost

        old = timezone.now() - STUCK_PUBLISHING_AFTER - timedelta(minutes=1)
        pp = self._pp(status=PlatformPost.Status.PUBLISHING, **fields)
        self._age(pp, updated_at=old, **({"publishing_started_at": old} if "publishing_started_at" in fields else {}))
        return pp

    def test_orphan_before_dispatch_is_requeued_and_republished(self):
        from apps.composer.models import PlatformPost

        pp = self._stuck(publishing_started_at=timezone.now())
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=SUCCESS) as dispatch:
            PublishEngine().poll_and_publish()
            # Requeued with the normal backoff, not hammered right away.
            dispatch.assert_not_called()
            pp.refresh_from_db()
            self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
            self.assertEqual(pp.retry_count, 1)
            self.assertGreater(pp.next_retry_at, timezone.now())

            self._age(pp, next_retry_at=timezone.now() - timedelta(seconds=1))
            PublishEngine().poll_and_publish()

        self.assertEqual(dispatch.call_count, 1)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        logs = list(PublishLog.objects.filter(platform_post=pp).order_by("created_at"))
        self.assertEqual(len(logs), 2)
        self.assertIn("vor der Übergabe", logs[0].error_message)

    def test_orphan_after_dispatch_fails_with_hint(self):
        from apps.composer.models import PlatformPost

        pp = self._stuck(publishing_started_at=timezone.now(), dispatched_at=timezone.now())
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=SUCCESS) as dispatch:
            PublishEngine().poll_and_publish()

        dispatch.assert_not_called()
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("Ob er erschienen ist, ist unklar", pp.publish_error)
        self.assertIn("Facebook", pp.publish_error)
        self.assertEqual(PublishLog.objects.filter(platform_post=pp).count(), 1)

    def test_legacy_orphan_without_stamps_fails_with_hint(self):
        """Exactly the two rows of 23.10.2026: no stamps, updated_at a day old."""
        from apps.composer.models import PlatformPost

        pp = self._stuck(retry_count=1, next_retry_at=timezone.now() - timedelta(days=1))
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=SUCCESS) as dispatch:
            PublishEngine().poll_and_publish()

        dispatch.assert_not_called()
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("nicht erfasst", pp.publish_error)
        self.assertEqual(PublishLog.objects.get(platform_post=pp).attempt_number, 2)

    def test_orphan_with_platform_id_is_not_retried(self):
        from apps.composer.models import PlatformPost

        pp = self._stuck(publishing_started_at=timezone.now(), platform_post_id="fb-999")
        PublishEngine()._recover_stuck_publishing()
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("fb-999", pp.publish_error)

    def test_orphan_before_dispatch_with_retries_exhausted_fails(self):
        from apps.composer.models import PlatformPost

        pp = self._stuck(publishing_started_at=timezone.now(), retry_count=MAX_RETRIES)
        PublishEngine()._recover_stuck_publishing()
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("Nichts ist erschienen", pp.publish_error)

    def test_recent_publishing_row_is_left_alone(self):
        from apps.composer.models import PlatformPost

        pp = self._pp(status=PlatformPost.Status.PUBLISHING, publishing_started_at=timezone.now())
        self.assertEqual(PublishEngine()._recover_stuck_publishing(), 0)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHING)
        self.assertFalse(PublishLog.objects.filter(platform_post=pp).exists())

    def test_rows_in_other_states_are_ignored(self):
        old = timezone.now() - timedelta(days=2)
        pp = self._pp(status="published", platform_post_id="x", published_at=old)
        self._age(pp, updated_at=old)
        self.assertEqual(PublishEngine()._recover_stuck_publishing(), 0)
        pp.refresh_from_db()
        self.assertEqual(pp.status, "published")
