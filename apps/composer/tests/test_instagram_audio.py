"""Composer side of the Instagram platform sound.

Covers the two halves that keep an Instagram-only extra honest: the picker
endpoint that lists Meta's trending catalogue, and the save path that stores
the chosen sound in ``PlatformPost.platform_extra`` (or clears it again).
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class InstagramAudioTestsBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(
            user=self.user,
            organization=self.org,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.client.force_login(self.user)

        self.instagram = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="17841400000000000",
            account_name="Orbita Media",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
            oauth_access_token="page-token",
        )


class InstagramAudioEndpointTests(InstagramAudioTestsBase):
    """GET /workspace/<id>/compose/instagram-audio/<account_id>/"""

    def setUp(self):
        super().setUp()
        self.url = reverse(
            "composer:instagram_audio",
            kwargs={"workspace_id": self.workspace.id, "account_id": self.instagram.id},
        )

    def _provider(self, tracks):
        provider = MagicMock()
        provider.list_audio.return_value = tracks
        return provider

    def test_without_query_returns_trending(self):
        provider = self._provider(
            [
                {
                    "id": "587784541076604",
                    "title": "Sommerregen",
                    "artist": "Komiku",
                    "duration_ms": 21000,
                    "cover_url": "",
                    "raw": {"anything": "kept out of the response"},
                }
            ]
        )
        with patch("providers.get_provider", return_value=provider):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["available"])
        self.assertTrue(data["trending"])
        self.assertEqual(
            data["tracks"],
            [
                {
                    "id": "587784541076604",
                    "title": "Sommerregen",
                    "artist": "Komiku",
                    "duration_ms": 21000,
                    "cover_url": "",
                }
            ],
        )
        provider.list_audio.assert_called_once()
        kwargs = provider.list_audio.call_args.kwargs
        self.assertEqual(kwargs["search_query"], "")
        self.assertEqual(kwargs["ig_user_id"], "17841400000000000")

    def test_query_is_forwarded_and_marks_the_result_as_a_search(self):
        provider = self._provider([])
        with patch("providers.get_provider", return_value=provider):
            response = self.client.get(self.url, {"q": " walking shoes "})

        self.assertFalse(response.json()["trending"])
        self.assertEqual(provider.list_audio.call_args.kwargs["search_query"], "walking shoes")

    def test_unknown_audio_type_falls_back_to_music(self):
        provider = self._provider([])
        with patch("providers.get_provider", return_value=provider):
            self.client.get(self.url, {"audio_type": "podcast"})

        self.assertEqual(provider.list_audio.call_args.kwargs["audio_type"], "music")

    def test_provider_failure_degrades_to_an_empty_list(self):
        """A missing extra must never look like a broken composer."""
        provider = MagicMock()
        provider.list_audio.side_effect = RuntimeError("Instagram down")
        with patch("providers.get_provider", return_value=provider):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["available"])
        self.assertEqual(data["tracks"], [])

    def test_disconnected_account_reports_unavailable_without_calling_meta(self):
        self.instagram.oauth_access_token = ""
        self.instagram.save(update_fields=["oauth_access_token"])
        provider = self._provider([])

        with patch("providers.get_provider", return_value=provider):
            response = self.client.get(self.url)

        self.assertFalse(response.json()["available"])
        provider.list_audio.assert_not_called()

    def test_non_instagram_account_is_404(self):
        tiktok = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="tiktok",
            account_platform_id="tt-1",
            account_name="TikTok",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        url = reverse(
            "composer:instagram_audio",
            kwargs={"workspace_id": self.workspace.id, "account_id": tiktok.id},
        )
        self.assertEqual(self.client.get(url).status_code, 404)


class InstagramAudioSaveTests(InstagramAudioTestsBase):
    """POST /workspace/<id>/composer/compose/save/ with the reel sound panel."""

    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="hello")
        self.pp = PlatformPost.objects.create(
            post=self.post,
            social_account=self.instagram,
            status=PlatformPost.Status.DRAFT,
        )
        self.save_url = reverse(
            "composer:save_post_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
        )

    def _payload(self, **overrides):
        acc = str(self.instagram.id)
        payload = {
            "action": "save_draft",
            "title": "Reel",
            "caption": "hello",
            "tags": "",
            "selected_accounts": acc,
            f"ig_audio_id_{acc}": "587784541076604",
            f"ig_audio_title_{acc}": "Sommerregen",
            f"ig_audio_artist_{acc}": "Komiku",
            f"ig_audio_volume_{acc}": "25",
            f"ig_video_volume_{acc}": "100",
        }
        payload.update(overrides)
        return payload

    def test_chosen_sound_lands_in_platform_extra(self):
        response = self.client.post(self.save_url, data=self._payload())
        self.assertIn(response.status_code, (200, 204, 302))

        self.pp.refresh_from_db()
        self.assertEqual(
            self.pp.platform_extra,
            {
                "audio_id": "587784541076604",
                "audio_volume": 25,
                "video_volume": 100,
                "audio_title": "Sommerregen",
                "audio_artist": "Komiku",
            },
        )

    def test_volumes_are_clamped_and_junk_falls_back_to_the_defaults(self):
        acc = str(self.instagram.id)
        response = self.client.post(
            self.save_url,
            data=self._payload(**{f"ig_audio_volume_{acc}": "500", f"ig_video_volume_{acc}": "loud"}),
        )
        self.assertIn(response.status_code, (200, 204, 302))

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_extra["audio_volume"], 100)
        self.assertEqual(self.pp.platform_extra["video_volume"], 100)

    def test_empty_selection_clears_a_previously_chosen_sound(self):
        self.pp.platform_extra = {
            "audio_id": "587784541076604",
            "audio_volume": 25,
            "video_volume": 100,
            "audio_title": "Sommerregen",
            "keep_me": "unrelated",
        }
        self.pp.save(update_fields=["platform_extra"])

        acc = str(self.instagram.id)
        response = self.client.post(self.save_url, data=self._payload(**{f"ig_audio_id_{acc}": ""}))
        self.assertIn(response.status_code, (200, 204, 302))

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_extra, {"keep_me": "unrelated"})

    def test_composer_page_renders_the_sound_panel(self):
        response = self.client.get(
            reverse(
                "composer:compose_edit",
                kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
            )
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("'ig_audio_id_' + accId", html)
        self.assertIn("compose/instagram-audio/", html)

    def test_save_without_the_panel_leaves_the_sound_untouched(self):
        """Saves from elsewhere in the app must not silently drop the sound."""
        self.pp.platform_extra = {"audio_id": "587784541076604", "audio_volume": 25, "video_volume": 100}
        self.pp.save(update_fields=["platform_extra"])

        response = self.client.post(
            self.save_url,
            data={
                "action": "save_draft",
                "title": "Reel",
                "caption": "hello",
                "tags": "",
                "selected_accounts": str(self.instagram.id),
            },
        )
        self.assertIn(response.status_code, (200, 204, 302))

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_extra["audio_id"], "587784541076604")
