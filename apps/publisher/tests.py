"""Tests for the Publishing Engine (T-1A.3)."""

import io
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.publisher.engine import MAX_RETRIES, RETRY_BACKOFF, PublishEngine, _resolve_publish_credentials
from apps.publisher.models import PublishLog, RateLimitState
from providers.exceptions import PublishError
from providers.types import AuthType, PostType, PublishResult


class RateLimitStateModelTest(TestCase):
    """Test RateLimitState model logic."""

    def test_is_rate_limited_when_zero_remaining_and_window_active(self):
        state = RateLimitState()
        state.requests_remaining = 0
        state.window_resets_at = timezone.now() + timedelta(minutes=5)
        self.assertTrue(state.is_rate_limited)

    def test_is_not_rate_limited_when_zero_remaining_and_window_expired(self):
        state = RateLimitState()
        state.requests_remaining = 0
        state.window_resets_at = timezone.now() - timedelta(minutes=5)
        self.assertFalse(state.is_rate_limited)

    def test_is_not_rate_limited_with_remaining_requests(self):
        state = RateLimitState()
        state.requests_remaining = 50
        state.window_resets_at = timezone.now() + timedelta(minutes=5)
        self.assertFalse(state.is_rate_limited)

    def test_can_publish_when_unknown(self):
        state = RateLimitState()
        state.requests_remaining = -1
        self.assertTrue(state.can_publish)

    def test_can_publish_when_remaining(self):
        state = RateLimitState()
        state.requests_remaining = 10
        self.assertTrue(state.can_publish)

    def test_cannot_publish_when_rate_limited(self):
        state = RateLimitState()
        state.requests_remaining = 0
        state.window_resets_at = timezone.now() + timedelta(minutes=5)
        self.assertFalse(state.can_publish)


class PublishEngineTest(TestCase):
    """Test PublishEngine core logic."""

    def test_retry_backoff_schedule(self):
        """Verify retry backoff values match spec."""
        self.assertEqual(RETRY_BACKOFF, [60, 300, 1800])
        self.assertEqual(MAX_RETRIES, 3)

    def test_engine_instantiates(self):
        engine = PublishEngine()
        self.assertIsNotNone(engine)

    @patch("apps.publisher.engine.PlatformPost.objects")
    def test_get_due_platform_posts_filters_correctly(self, mock_objects):
        """Engine should query PlatformPosts with a Coalesce effective_at filter."""
        engine = PublishEngine()
        mock_qs = MagicMock()
        mock_objects.filter.return_value = mock_qs
        mock_qs.annotate.return_value = mock_qs
        mock_qs.filter.return_value = mock_qs
        mock_qs.select_related.return_value = mock_qs
        mock_qs.order_by.return_value = mock_qs
        mock_qs.__getitem__ = MagicMock(return_value=[])

        engine._get_due_platform_posts()

        # First filter: editorial status (now lives on PlatformPost itself)
        first_call = mock_objects.filter.call_args_list[0]
        self.assertIn("status", first_call.kwargs)
        # Second filter (on annotated qs): effective_at__lte
        second_call = mock_qs.filter.call_args_list[0]
        self.assertIn("effective_at__lte", second_call.kwargs)


class PublishLogModelTest(TestCase):
    """Test PublishLog model."""

    def test_str_representation(self):
        log = PublishLog()
        log.attempt_number = 2
        log.status_code = 200
        s = str(log)
        self.assertIn("2", s)
        self.assertIn("200", s)


class _FakeStoredFile:
    """Stand-in for a Django FileField value backed by in-memory bytes."""

    def __init__(self, url: str, data: bytes = b"fake-image-bytes"):
        self.url = url
        self._data = data

    def open(self, mode: str = "rb"):
        return io.BytesIO(self._data)


def _fake_attachment(filename: str, alt_text: str = "", asset_alt_text: str = "", media_type: str = "image"):
    """Build a PostMedia-shaped stub whose asset streams real bytes.

    ``alt_text`` is the per-attachment override, ``asset_alt_text`` the alt
    text stored on the shared media asset.
    """
    asset = MagicMock()
    asset.file = _FakeStoredFile(f"https://cdn.example.com/{filename}")
    asset.filename = filename
    asset.media_type = media_type
    asset.alt_text = asset_alt_text
    asset.duration = 0

    attachment = MagicMock()
    attachment.media_asset = asset
    attachment.alt_text = alt_text
    return attachment


def _build_dispatch_mocks(
    platform: str,
    account_platform_id: str,
    platform_extra: dict | None = None,
    attachments: list | None = None,
):
    """Build the minimal mocks needed to exercise _dispatch_to_provider's
    extras-assembly without DB or filesystem side effects.

    Returns (engine, platform_post, mock_provider).
    """
    engine = PublishEngine()

    account = MagicMock()
    account.platform = platform
    account.account_platform_id = account_platform_id
    account.token_expires_at = None  # skip the OAuth refresh branch
    account.oauth_access_token = "tok"
    account.account_name = "Test Account"

    platform_post = MagicMock()
    platform_post.social_account = account
    platform_post.post.media_attachments.select_related.return_value.order_by.return_value = attachments or []
    platform_post.post.tags = []
    platform_post.effective_caption = "hello"
    platform_post.effective_title = None
    platform_post.effective_first_comment = None
    platform_post.platform_extra = platform_extra or {}

    mock_provider = MagicMock()
    mock_provider.auth_type = AuthType.OAUTH2
    mock_provider.supported_post_types = [PostType.TEXT]
    mock_provider.publish_post.return_value = PublishResult(
        platform_post_id="post-1",
        url="https://example.com/p/1",
        extra={},
    )
    return engine, platform_post, mock_provider


class DispatchExtraInjectionTest(SimpleTestCase):
    """Verify _dispatch_to_provider injects platform-specific extras."""

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_injects_organization_author_for_linkedin_company(self, _mock_creds, mock_get_provider):
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="linkedin_company",
            account_platform_id="98765",
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        mock_provider.publish_post.assert_called_once()
        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(content.extra.get("author"), "urn:li:organization:98765")

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_injects_ig_user_id_for_instagram(self, _mock_creds, mock_get_provider):
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="instagram",
            account_platform_id="17841400000000000",
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        mock_provider.publish_post.assert_called_once()
        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(content.extra.get("ig_user_id"), "17841400000000000")

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_does_not_overwrite_explicit_author(self, _mock_creds, mock_get_provider):
        # When the caller has already set extra["author"], the engine must not
        # overwrite it — important for callers that pass a different URN.
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="linkedin_company",
            account_platform_id="98765",
            platform_extra={"author": "urn:li:organization:override"},
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(content.extra.get("author"), "urn:li:organization:override")

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_does_not_inject_author_for_other_platforms(self, _mock_creds, mock_get_provider):
        # Sanity: the author-injection branch is scoped to linkedin_company only.
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="linkedin_personal",
            account_platform_id="11111",
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertNotIn("author", content.extra)


class DispatchAltTextTest(SimpleTestCase):
    """Verify _dispatch_to_provider hands one alt text per media item down.

    Regression guard: the engine used to build media_files/media_urls without
    ever collecting the attachments' alt text, so every provider published
    images with no accessibility description at all.
    """

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_passes_one_alt_text_per_attachment_in_order(self, _mock_creds, mock_get_provider):
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="bluesky",
            account_platform_id="did:plc:test",
            attachments=[
                _fake_attachment("slide-1.png", asset_alt_text="Folie 1: Überschrift"),
                _fake_attachment("slide-2.png", asset_alt_text="Folie 2: Zahlen"),
                _fake_attachment("slide-3.png", asset_alt_text="Folie 3: Fazit"),
            ],
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(
            content.media_alt_texts,
            ["Folie 1: Überschrift", "Folie 2: Zahlen", "Folie 3: Fazit"],
        )
        # Positional alignment with the media lists is what keeps a slide's
        # description on that slide.
        self.assertEqual(len(content.media_alt_texts), len(content.media_files))
        self.assertEqual(len(content.media_alt_texts), len(content.media_urls))

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_attachment_override_wins_over_asset_alt_text(self, _mock_creds, mock_get_provider):
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="bluesky",
            account_platform_id="did:plc:test",
            attachments=[
                _fake_attachment("a.png", alt_text="Für diesen Beitrag angepasst", asset_alt_text="Standardtext"),
                _fake_attachment("b.png", asset_alt_text="Standardtext B"),
            ],
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(content.media_alt_texts, ["Für diesen Beitrag angepasst", "Standardtext B"])

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_missing_alt_text_keeps_position_and_does_not_fail(self, _mock_creds, mock_get_provider):
        # A gap must stay a gap: shifting entries up would move slide 3's
        # description onto slide 2, which is worse than no alt text at all.
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="bluesky",
            account_platform_id="did:plc:test",
            attachments=[
                _fake_attachment("a.png", asset_alt_text="Erste Folie"),
                _fake_attachment("b.png"),
                _fake_attachment("c.png", asset_alt_text="Dritte Folie"),
            ],
        )
        mock_get_provider.return_value = mock_provider

        result = engine._dispatch_to_provider(platform_post)

        self.assertTrue(result["success"])
        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(content.media_alt_texts, ["Erste Folie", "", "Dritte Folie"])

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_no_attachments_yields_empty_alt_text_list(self, _mock_creds, mock_get_provider):
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="bluesky",
            account_platform_id="did:plc:test",
        )
        mock_get_provider.return_value = mock_provider

        engine._dispatch_to_provider(platform_post)

        _access_token, content = mock_provider.publish_post.call_args.args
        self.assertEqual(content.media_alt_texts, [])


class ResolvePublishCredentialsTest(SimpleTestCase):
    @patch("apps.publisher.engine.resolve_platform_credentials", return_value={"client_id": "id"})
    def test_facebook_credentials_include_selected_page_id(self, _mock_resolve):
        account = MagicMock()
        account.platform = "facebook"
        account.account_platform_id = "page-1"
        account.workspace.organization_id = "org-1"

        credentials = _resolve_publish_credentials(account)

        self.assertEqual(credentials["page_id"], "page-1")

    @patch("apps.publisher.engine.resolve_platform_credentials", return_value={"client_id": "id"})
    def test_instagram_credentials_include_selected_ig_user_id(self, _mock_resolve):
        account = MagicMock()
        account.platform = "instagram"
        account.account_platform_id = "17841400000000000"
        account.workspace.organization_id = "org-1"

        credentials = _resolve_publish_credentials(account)

        self.assertEqual(credentials["ig_user_id"], "17841400000000000")

    @patch("apps.common.validators.is_safe_url", return_value=True)
    @patch("apps.publisher.engine.resolve_platform_credentials", return_value={})
    def test_bluesky_safe_pds_url_is_injected(self, _mock_resolve, _mock_is_safe_url):
        account = MagicMock()
        account.platform = "bluesky"
        account.instance_url = "https://pds.example.com"
        account.workspace.organization_id = "org-1"

        credentials = _resolve_publish_credentials(account)

        self.assertEqual(credentials["pds_url"], "https://pds.example.com")

    @patch("apps.common.validators.is_safe_url", return_value=False)
    @patch("apps.publisher.engine.resolve_platform_credentials", return_value={})
    def test_bluesky_unsafe_pds_url_is_rejected(self, _mock_resolve, _mock_is_safe_url):
        # The Bluesky pds_url sets the outbound host, so a URL that fails the SSRF
        # check must not reach the provider — parity with the Mastodon gate.
        account = MagicMock()
        account.platform = "bluesky"
        account.instance_url = "http://169.254.169.254"
        account.workspace.organization_id = "org-1"

        credentials = _resolve_publish_credentials(account)

        self.assertNotIn("pds_url", credentials)


class NonRetryableFailureTest(TestCase):
    """_publish_platform_post must honor the exception's ``retryable`` flag."""

    def setUp(self):
        from apps.composer.models import PlatformPost, Post
        from apps.organizations.models import Organization
        from apps.social_accounts.models import SocialAccount
        from apps.workspaces.models import Workspace

        self.org = Organization.objects.create(name="Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="WS")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="tiktok",
            account_platform_id="tt-1",
            account_name="janschmitz51",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.post = Post.objects.create(workspace=self.workspace, caption="hi")
        self.platform_post = PlatformPost.objects.create(
            post=self.post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHING,
        )

    def test_non_retryable_error_fails_immediately(self):
        from apps.composer.models import PlatformPost
        from providers.exceptions import PublishError

        engine = PublishEngine()
        error = PublishError("TikTok rejected the post: audit pending", platform="TikTok", retryable=False)
        with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=error):
            result = engine._publish_platform_post(self.platform_post)

        self.assertFalse(result["success"])
        self.platform_post.refresh_from_db()
        self.assertEqual(self.platform_post.status, PlatformPost.Status.FAILED)
        self.assertEqual(self.platform_post.retry_count, 0)
        self.assertIsNone(self.platform_post.next_retry_at)
        self.assertIn("audit pending", self.platform_post.publish_error)
        self.assertEqual(PublishLog.objects.filter(platform_post=self.platform_post).count(), 1)

    def test_retryable_error_schedules_backoff_retry(self):
        from apps.composer.models import PlatformPost
        from providers.exceptions import PublishError

        engine = PublishEngine()
        error = PublishError("transient", platform="TikTok")
        with patch.object(PublishEngine, "_dispatch_to_provider", side_effect=error):
            result = engine._publish_platform_post(self.platform_post)

        self.assertFalse(result["success"])
        self.platform_post.refresh_from_db()
        self.assertEqual(self.platform_post.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(self.platform_post.retry_count, 1)
        self.assertIsNotNone(self.platform_post.next_retry_at)
        self.assertEqual(PublishLog.objects.filter(platform_post=self.platform_post).count(), 1)


class PublishedPostLeavesQueueTest(TestCase):
    """A successful publish drops the post's QueueEntry, freeing the slot."""

    def setUp(self):
        from apps.calendar.models import Queue, QueueEntry
        from apps.composer.models import PlatformPost, Post
        from apps.organizations.models import Organization
        from apps.social_accounts.models import SocialAccount
        from apps.workspaces.models import Workspace

        self.org = Organization.objects.create(name="Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="WS")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="linkedin_personal",
            account_platform_id="li-1",
            account_name="LI",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.queue = Queue.objects.create(workspace=self.workspace, name="Q", social_account=self.account)
        self.post = Post.objects.create(workspace=self.workspace, caption="hi")
        self.pp = PlatformPost.objects.create(
            post=self.post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHING,
            scheduled_at=timezone.now() + timedelta(hours=1),
        )
        self.entry = QueueEntry.objects.create(
            queue=self.queue, post=self.post, position=0, assigned_slot_datetime=self.pp.scheduled_at
        )

    def test_publish_success_removes_queue_entry(self):
        from apps.calendar.models import QueueEntry
        from apps.composer.models import PlatformPost

        engine = PublishEngine()
        success = {"success": True, "platform_post_id": "x", "status_code": 200, "response": {}}
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=success):
            engine._publish_platform_post(self.pp)

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.status, PlatformPost.Status.PUBLISHED)
        self.assertIsNotNone(self.pp.published_at)
        # The QueueEntry is gone (slot freed), but the PlatformPost remains.
        self.assertFalse(QueueEntry.objects.filter(id=self.entry.id).exists())

    def test_publish_success_stores_response_extra_on_platform_extra(self):
        from apps.composer.models import PlatformPost

        self.pp.platform_extra = {"post_type": "text"}
        self.pp.save(update_fields=["platform_extra"])

        engine = PublishEngine()
        success = {
            "success": True,
            "platform_post_id": "post-1",
            "status_code": 200,
            "response": {"id": "page-1_post-1", "tracking": {"source": "graph"}},
        }
        with patch.object(PublishEngine, "_dispatch_to_provider", return_value=success):
            engine._publish_platform_post(self.pp)

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(self.pp.platform_post_id, "post-1")
        self.assertEqual(
            self.pp.platform_extra,
            {"post_type": "text", "id": "page-1_post-1", "tracking": {"source": "graph"}},
        )

    def test_publish_success_survives_queue_cleanup_failure(self):
        from apps.composer.models import PlatformPost

        engine = PublishEngine()
        success = {"success": True, "platform_post_id": "x", "status_code": 200, "response": {}}
        # The post is durably published; if the QueueEntry cleanup then fails it
        # must NOT fall through to a retry (which would re-dispatch and double-post).
        with (
            patch.object(PublishEngine, "_dispatch_to_provider", return_value=success),
            patch("apps.calendar.models.QueueEntry.objects") as mock_objects,
        ):
            mock_objects.filter.side_effect = Exception("db unavailable")
            engine._publish_platform_post(self.pp)

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(self.pp.retry_count, 0)
        self.assertIsNone(self.pp.next_retry_at)


class DispatchFremdzeichenSperreTest(SimpleTestCase):
    """Die letzte Sperre vor dem Veröffentlichen (siehe apps.common.homoglyphs).

    Anlass: In einem fertigen Beitrag stand „überspringst" mit kyrillischem
    и, н und г. Der Text war fehlerfrei zu lesen und wäre so live gegangen.
    """

    @staticmethod
    def _mocks(caption="hallo", alt_texts=None):
        engine, platform_post, mock_provider = _build_dispatch_mocks(
            platform="bluesky",
            account_platform_id="did:plc:abc",
            attachments=[
                _fake_attachment(f"{i}.jpg", asset_alt_text=alt) for i, alt in enumerate(alt_texts or [], start=1)
            ],
        )
        platform_post.effective_caption = caption
        return engine, platform_post, mock_provider

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_sauberer_text_geht_durch(self, _creds, mock_get_provider):
        engine, platform_post, mock_provider = self._mocks(
            caption="Wenn du das überspringst, verlierst du den Anschluss. 🚀",
            alt_texts=["Folie 1: Größe zählt", "Folie 2: Übung macht den Meister"],
        )
        mock_get_provider.return_value = mock_provider

        result = engine._dispatch_to_provider(platform_post)

        self.assertTrue(result["success"])

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_kyrillische_zeichen_im_beitragstext_verhindern_das_posten(self, _creds, mock_get_provider):
        engine, platform_post, mock_provider = self._mocks(caption="Wenn du das übersprингst")
        mock_get_provider.return_value = mock_provider

        with self.assertRaises(PublishError) as ctx:
            engine._dispatch_to_provider(platform_post)

        self.assertFalse(ctx.exception.retryable)
        self.assertIn("Beitragstext", str(ctx.exception))
        self.assertIn("U+0438", str(ctx.exception))
        mock_provider.publish_post.assert_not_called()

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_auch_ein_alternativtext_haelt_den_beitrag_auf(self, _creds, mock_get_provider):
        engine, platform_post, mock_provider = self._mocks(
            caption="alles sauber",
            alt_texts=["Folie 1: sauber", "Folie 2: сauber geschrieben"],
        )
        mock_get_provider.return_value = mock_provider

        with self.assertRaises(PublishError) as ctx:
            engine._dispatch_to_provider(platform_post)

        self.assertIn("Alternativtext Bild 2", str(ctx.exception))
        mock_provider.publish_post.assert_not_called()

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_unsichtbares_zeichen_haelt_den_beitrag_auf(self, _creds, mock_get_provider):
        engine, platform_post, mock_provider = self._mocks(caption="Zero​Width im Text")
        mock_get_provider.return_value = mock_provider

        with self.assertRaises(PublishError) as ctx:
            engine._dispatch_to_provider(platform_post)

        self.assertIn("U+200B", str(ctx.exception))

    @patch("apps.publisher.engine.get_provider")
    @patch("apps.publisher.engine._resolve_publish_credentials", return_value={})
    def test_fremdsprachiges_zitat_geht_durch(self, _creds, mock_get_provider):
        engine, platform_post, mock_provider = self._mocks(caption="Ein Zitat: καλημέρα")
        mock_get_provider.return_value = mock_provider

        result = engine._dispatch_to_provider(platform_post)

        self.assertTrue(result["success"])
