"""Composer side of the story switch ("Als Story veröffentlichen").

Instagram (both connection types) and Facebook get a story panel whose hidden
field ``story_<acc>`` always submits "true"/"false", so switching it off
really removes the story. A save without the panel leaves a stored story
alone, and every other key in ``platform_extra`` – sound, collaborators,
trial, cover – survives, the same contract as the trial and cover panels.
"""

from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.composer.tests.test_instagram_audio import InstagramAudioTestsBase
from apps.social_accounts.models import SocialAccount


class StorySwitchSaveTests(InstagramAudioTestsBase):
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

    def _account(self, platform, platform_id):
        return SocialAccount.objects.create(
            workspace=self.workspace,
            platform=platform,
            account_platform_id=platform_id,
            account_name=f"Orbita {platform}",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )

    def _payload(self, account=None, *, story=None, **overrides):
        acc = str((account or self.instagram).id)
        payload = {
            "action": "save_draft",
            "title": "Story",
            "caption": "hello",
            "tags": "",
            "selected_accounts": acc,
        }
        if story is not None:
            payload[f"story_{acc}"] = story
        payload.update(overrides)
        return payload

    def _save(self, payload, pp=None):
        response = self.client.post(self.save_url, data=payload)
        self.assertIn(response.status_code, (200, 204, 302), response.content[:500])
        (pp or self.pp).refresh_from_db()
        return pp or self.pp

    def test_switch_on_stores_the_story_and_keeps_everything_else(self):
        self.pp.platform_extra = {
            "collaborators": ["autorin"],
            "trial": True,
            "trial_graduation": "MANUAL",
            "thumbnail_asset_id": "c0ffee00-0000-0000-0000-000000000000",
            "thumb_offset_ms": 900,
            "audio_id": "587784541076604",
        }
        self.pp.save(update_fields=["platform_extra"])

        self._save(self._payload(story="true"))

        self.assertEqual(
            self.pp.platform_extra,
            {
                "collaborators": ["autorin"],
                "trial": True,
                "trial_graduation": "MANUAL",
                "thumbnail_asset_id": "c0ffee00-0000-0000-0000-000000000000",
                "thumb_offset_ms": 900,
                "audio_id": "587784541076604",
                "post_type": "story",
            },
        )

    def test_switch_off_removes_only_the_story(self):
        self.pp.platform_extra = {"post_type": "story", "collaborators": ["autorin"]}
        self.pp.save(update_fields=["platform_extra"])

        self._save(self._payload(story="false"))

        self.assertEqual(self.pp.platform_extra, {"collaborators": ["autorin"]})

    def test_save_without_the_panel_leaves_the_story_untouched(self):
        self.pp.platform_extra = {"post_type": "story"}
        self.pp.save(update_fields=["platform_extra"])

        self._save(self._payload())

        self.assertEqual(self.pp.platform_extra, {"post_type": "story"})

    def test_story_survives_the_sound_trial_and_cover_panels_in_the_same_save(self):
        acc = str(self.instagram.id)
        self._save(
            self._payload(
                story="true",
                **{
                    f"ig_audio_id_{acc}": "",
                    f"ig_trial_{acc}": "false",
                    f"ig_trial_graduation_{acc}": "SS_PERFORMANCE",
                    f"cover_panel_{acc}": "1",
                    f"cover_asset_id_{acc}": "",
                    f"cover_offset_ms_{acc}": "",
                },
            )
        )
        self.assertEqual(self.pp.platform_extra, {"post_type": "story"})

    def test_facebook_and_instagram_login_take_the_switch(self):
        for platform, pid in (("facebook", "page-1"), ("instagram_login", "ig-login-1")):
            account = self._account(platform, pid)
            pp = PlatformPost.objects.create(post=self.post, social_account=account, status=PlatformPost.Status.DRAFT)
            self._save(self._payload(account=account, story="true"), pp=pp)
            self.assertEqual(pp.platform_extra, {"post_type": "story"}, platform)

    def test_other_platforms_ignore_the_switch(self):
        bluesky = self._account("bluesky", "bsky-1")
        pp = PlatformPost.objects.create(post=self.post, social_account=bluesky, status=PlatformPost.Status.DRAFT)
        self._save(self._payload(account=bluesky, story="true"), pp=pp)
        self.assertNotIn("post_type", pp.platform_extra or {})

    def test_composer_page_renders_the_story_panel(self):
        self.pp.platform_extra = {"post_type": "story"}
        self.pp.save(update_fields=["platform_extra"])
        response = self.client.get(
            reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id})
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("Als Story veröffentlichen", html)
        self.assertIn("'story_' + accId", html)
        self.assertIn("data-story-panel", html)
        self.assertEqual(response.context["platform_extras"][str(self.instagram.id)]["post_type"], "story")
