"""Composer side of the Titelbild (cover) of a video post.

Instagram and Facebook get a "Titelbild" panel whose hidden fields always
submit (``cover_panel_``, ``cover_asset_id_``, ``cover_offset_ms_``), so
clearing really clears. A save without the panel leaves a stored cover
alone, and everything else in ``platform_extra`` – sound, collaborators,
trial – survives, exactly like the trial switch. TikTok keeps its own cover
frame field, now mirrored into the channel-neutral ``thumb_offset_ms`` the
Agent-API reads.
"""

from django.core.files.base import ContentFile
from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.composer.tests.test_instagram_audio import InstagramAudioTestsBase
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class VideoCoverSaveTests(InstagramAudioTestsBase):
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
        self.jpeg = self._asset(self.workspace, "hook.jpg", "image", "image/jpeg")

    def _asset(self, workspace, name, media_type, mime):
        asset = MediaAsset(
            organization=workspace.organization,
            workspace=workspace,
            filename=name,
            file=ContentFile(b"\xff\xd8" + b"\x00" * 30, name=name),
            media_type=media_type,
            mime_type=mime,
            file_size=32,
            processing_status="completed",
        )
        asset.save()
        return asset

    def _account(self, platform, platform_id):
        return SocialAccount.objects.create(
            workspace=self.workspace,
            platform=platform,
            account_platform_id=platform_id,
            account_name=f"Orbita {platform}",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )

    def _payload(self, account=None, *, asset="", offset="", panel=True, **overrides):
        acc = str((account or self.instagram).id)
        payload = {
            "action": "save_draft",
            "title": "Reel",
            "caption": "hello",
            "tags": "",
            "selected_accounts": acc,
        }
        if panel:
            payload[f"cover_panel_{acc}"] = "1"
            payload[f"cover_asset_id_{acc}"] = asset
            payload[f"cover_offset_ms_{acc}"] = offset
        payload.update(overrides)
        return payload

    def _save(self, payload, pp=None):
        response = self.client.post(self.save_url, data=payload)
        self.assertIn(response.status_code, (200, 204, 302), response.content[:500])
        (pp or self.pp).refresh_from_db()
        return pp or self.pp

    def test_image_and_frame_land_in_platform_extra(self):
        self._save(self._payload(asset=str(self.jpeg.id), offset="2400"))
        self.assertEqual(self.pp.platform_extra, {"thumbnail_asset_id": str(self.jpeg.id), "thumb_offset_ms": 2400})

    def test_clearing_removes_both_and_keeps_everything_else(self):
        self.pp.platform_extra = {
            "thumbnail_asset_id": str(self.jpeg.id),
            "thumb_offset_ms": 100,
            "collaborators": ["autorin"],
            "trial": True,
            "trial_graduation": "MANUAL",
        }
        self.pp.save(update_fields=["platform_extra"])

        self._save(self._payload())

        self.assertEqual(
            self.pp.platform_extra, {"collaborators": ["autorin"], "trial": True, "trial_graduation": "MANUAL"}
        )

    def test_cover_survives_next_to_sound_and_trial_panels(self):
        acc = str(self.instagram.id)
        self._save(
            self._payload(
                asset=str(self.jpeg.id),
                **{
                    f"ig_audio_id_{acc}": "587784541076604",
                    f"ig_audio_volume_{acc}": "25",
                    f"ig_video_volume_{acc}": "100",
                    f"ig_trial_{acc}": "true",
                    f"ig_trial_graduation_{acc}": "SS_PERFORMANCE",
                },
            )
        )
        extra = self.pp.platform_extra
        self.assertEqual(extra["audio_id"], "587784541076604")
        self.assertIs(extra["trial"], True)
        self.assertEqual(extra["thumbnail_asset_id"], str(self.jpeg.id))

    def test_save_without_the_panel_leaves_the_cover_untouched(self):
        self.pp.platform_extra = {"thumbnail_asset_id": str(self.jpeg.id), "thumb_offset_ms": 50}
        self.pp.save(update_fields=["platform_extra"])

        self._save(self._payload(panel=False))

        self.assertEqual(self.pp.platform_extra, {"thumbnail_asset_id": str(self.jpeg.id), "thumb_offset_ms": 50})

    def test_foreign_video_or_garbage_asset_is_dropped(self):
        other = Workspace.objects.create(name="Other", organization=self.workspace.organization)
        foreign = self._asset(other, "x.jpg", "image", "image/jpeg")
        video = self._asset(self.workspace, "reel.mp4", "video", "video/mp4")
        for bad in (str(foreign.id), str(video.id), "not-a-uuid"):
            self._save(self._payload(asset=bad, offset="10"))
            self.assertEqual(self.pp.platform_extra, {"thumb_offset_ms": 10}, bad)

    def test_bad_offset_is_dropped(self):
        for bad in ("-5", "abc", "²"):
            self._save(self._payload(offset=bad))
            self.assertEqual(self.pp.platform_extra, {}, bad)

    def test_facebook_takes_the_image_but_no_frame(self):
        fb = self._account("facebook", "page-1")
        pp = PlatformPost.objects.create(post=self.post, social_account=fb, status=PlatformPost.Status.DRAFT)
        self._save(self._payload(account=fb, asset=str(self.jpeg.id), offset="300"), pp=pp)
        self.assertEqual(pp.platform_extra, {"thumbnail_asset_id": str(self.jpeg.id)})

    def test_instagram_login_uses_the_panel_too(self):
        login = self._account("instagram_login", "ig-login-1")
        pp = PlatformPost.objects.create(post=self.post, social_account=login, status=PlatformPost.Status.DRAFT)
        self._save(self._payload(account=login, offset="700"), pp=pp)
        self.assertEqual(pp.platform_extra, {"thumb_offset_ms": 700})

    def test_other_platforms_ignore_the_panel(self):
        bluesky = self._account("bluesky", "bsky-1")
        pp = PlatformPost.objects.create(post=self.post, social_account=bluesky, status=PlatformPost.Status.DRAFT)
        self._save(self._payload(account=bluesky, asset=str(self.jpeg.id), offset="1"), pp=pp)
        self.assertEqual(pp.platform_extra or {}, {})

    def test_tiktok_cover_frame_is_mirrored_to_the_neutral_key(self):
        tiktok = self._account("tiktok", "tt-1")
        pp = PlatformPost.objects.create(post=self.post, social_account=tiktok, status=PlatformPost.Status.DRAFT)
        acc = str(tiktok.id)
        base = self._payload(
            account=tiktok,
            panel=False,
            **{
                f"tiktok_privacy_level_{acc}": "SELF_ONLY",
                f"tiktok_video_cover_timestamp_ms_{acc}": "3300",
            },
        )
        self._save(base, pp=pp)
        self.assertEqual(pp.platform_extra["video_cover_timestamp_ms"], 3300)
        self.assertEqual(pp.platform_extra["thumb_offset_ms"], 3300)

        base[f"tiktok_video_cover_timestamp_ms_{acc}"] = ""
        self._save(base, pp=pp)
        self.assertNotIn("video_cover_timestamp_ms", pp.platform_extra)
        self.assertNotIn("thumb_offset_ms", pp.platform_extra)

    def test_composer_page_renders_the_cover_panel_with_the_stored_image(self):
        self.pp.platform_extra = {"thumbnail_asset_id": str(self.jpeg.id), "thumb_offset_ms": 900}
        self.pp.save(update_fields=["platform_extra"])
        response = self.client.get(
            reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id})
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("'cover_asset_id_' + accId", html)
        self.assertIn("' - Titelbild'", html)
        self.assertIn("Profilraster 3:4", html)
        self.assertIn("Frame per Regler", html)
        extras = response.context["platform_extras"][str(self.instagram.id)]
        self.assertEqual(extras["thumbnail_mime"], "image/jpeg")
        self.assertTrue(extras["thumbnail_url"])

    def test_thumbnail_upload_reports_the_mime_type(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            reverse("composer:thumbnail_upload", kwargs={"workspace_id": self.workspace.id}),
            data={"file": SimpleUploadedFile("frame.jpg", b"\xff\xd8\xff\xe0jpeg", content_type="image/jpeg")},
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(response.json()["mime_type"], "image/jpeg")
