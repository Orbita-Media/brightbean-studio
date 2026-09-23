"""Composer side of the Instagram trial reel ("Test-Reel").

The switch and the graduation choice ride in two hidden fields that always
submit, so switching the trial off really removes it. A save from a form
without the panel must leave a stored trial alone, and the trial must
survive next to a chosen sound – both live in ``platform_extra``.
"""

from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.composer.tests.test_instagram_audio import InstagramAudioTestsBase
from apps.social_accounts.models import SocialAccount


class InstagramTrialSaveTests(InstagramAudioTestsBase):
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

    def _payload(self, account=None, **overrides):
        acc = str((account or self.instagram).id)
        payload = {
            "action": "save_draft",
            "title": "Reel",
            "caption": "hello",
            "tags": "",
            "selected_accounts": acc,
            f"ig_trial_{acc}": "true",
            f"ig_trial_graduation_{acc}": "SS_PERFORMANCE",
        }
        payload.update(overrides)
        return payload

    def _save(self, payload):
        response = self.client.post(self.save_url, data=payload)
        self.assertIn(response.status_code, (200, 204, 302), response.content[:500])
        self.pp.refresh_from_db()

    def test_switch_on_lands_in_platform_extra_with_the_default(self):
        self._save(self._payload())
        self.assertEqual(self.pp.platform_extra, {"trial": True, "trial_graduation": "SS_PERFORMANCE"})

    def test_manual_graduation_is_stored(self):
        acc = str(self.instagram.id)
        self._save(self._payload(**{f"ig_trial_graduation_{acc}": "MANUAL"}))
        self.assertEqual(self.pp.platform_extra["trial_graduation"], "MANUAL")

    def test_unknown_graduation_falls_back_to_the_default(self):
        acc = str(self.instagram.id)
        self._save(self._payload(**{f"ig_trial_graduation_{acc}": "AUTO"}))
        self.assertEqual(self.pp.platform_extra["trial_graduation"], "SS_PERFORMANCE")

    def test_switch_off_removes_it_and_keeps_everything_else(self):
        self.pp.platform_extra = {"trial": True, "trial_graduation": "MANUAL", "collaborators": ["autorin"]}
        self.pp.save(update_fields=["platform_extra"])

        acc = str(self.instagram.id)
        self._save(self._payload(**{f"ig_trial_{acc}": "false"}))

        self.assertEqual(self.pp.platform_extra, {"collaborators": ["autorin"]})

    def test_trial_survives_next_to_a_chosen_sound(self):
        acc = str(self.instagram.id)
        self._save(
            self._payload(
                **{
                    f"ig_audio_id_{acc}": "587784541076604",
                    f"ig_audio_volume_{acc}": "25",
                    f"ig_video_volume_{acc}": "100",
                    f"ig_trial_graduation_{acc}": "MANUAL",
                }
            )
        )
        self.assertEqual(self.pp.platform_extra["audio_id"], "587784541076604")
        self.assertIs(self.pp.platform_extra["trial"], True)
        self.assertEqual(self.pp.platform_extra["trial_graduation"], "MANUAL")

    def test_save_without_the_panel_leaves_the_trial_untouched(self):
        self.pp.platform_extra = {"trial": True, "trial_graduation": "MANUAL"}
        self.pp.save(update_fields=["platform_extra"])

        acc = str(self.instagram.id)
        payload = self._payload()
        del payload[f"ig_trial_{acc}"]
        del payload[f"ig_trial_graduation_{acc}"]
        self._save(payload)

        self.assertEqual(self.pp.platform_extra, {"trial": True, "trial_graduation": "MANUAL"})

    def test_instagram_login_account_stores_the_trial_too(self):
        login = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram_login",
            account_platform_id="ig-login-1",
            account_name="Orbita via Instagram Login",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        pp = PlatformPost.objects.create(post=self.post, social_account=login, status=PlatformPost.Status.DRAFT)

        response = self.client.post(self.save_url, data=self._payload(account=login))
        self.assertIn(response.status_code, (200, 204, 302))

        pp.refresh_from_db()
        self.assertEqual(pp.platform_extra, {"trial": True, "trial_graduation": "SS_PERFORMANCE"})

    def test_other_platforms_ignore_the_field(self):
        bluesky = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="bluesky",
            account_platform_id="bsky-1",
            account_name="Bluesky",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        pp = PlatformPost.objects.create(post=self.post, social_account=bluesky, status=PlatformPost.Status.DRAFT)

        response = self.client.post(self.save_url, data=self._payload(account=bluesky))
        self.assertIn(response.status_code, (200, 204, 302))

        pp.refresh_from_db()
        self.assertNotIn("trial", pp.platform_extra or {})

    def test_composer_page_renders_the_trial_panel(self):
        response = self.client.get(
            reverse(
                "composer:compose_edit",
                kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
            )
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("'ig_trial_' + accId", html)
        self.assertIn("Test-Reel (zuerst nur Nicht-Follower)", html)
        self.assertIn("SS_PERFORMANCE", html)
