"""Agent API side of Instagram trial reels ("Test-Reel").

``POST /api/v1/posts`` takes the switch through
``platform_overrides[].trial`` (+ optional ``trial_graduation``), which lands
in ``PlatformPost.platform_extra`` as ``trial`` / ``trial_graduation`` and
reaches the provider on the same path as every other platform setting.

It has to work on CREATE: drafts in the distributor are never brought to a
new state by PATCH (the channel-specific short version would be lost), so a
trial reel must be creatable in one call. PATCH is supported as well, with
the usual "absent field keeps the stored value" rule.

The GET response shows the switch, so a caller can prove what is stored
instead of assuming it.
"""

from __future__ import annotations

import json

import pytest
from django.core.files.base import ContentFile

# ruff: noqa: F401, F811  (imported for pytest fixture resolution, not called here)
from apps.api.tests.test_instagram_audio_api import (
    _SecureClient,
    bluesky_account,
    client_with_token,
    instagram_account,
    issued_key,
    organization,
    owner_memberships,
    user,
    workspace,
)
from apps.composer.models import PlatformPost


def _post(client, body: dict):
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _patch(client, post_id, body: dict):
    return client.patch(f"/api/v1/posts/{post_id}", data=json.dumps(body), content_type="application/json")


def _asset(workspace, name: str, media_type: str):
    from apps.media_library.models import MediaAsset

    content = ContentFile(b"\x00" * 32, name=name)
    asset = MediaAsset(
        organization=workspace.organization,
        workspace=workspace,
        filename=name,
        file=content,
        media_type=media_type,
        mime_type="video/mp4" if media_type == "video" else "image/png",
        file_size=content.size,
        processing_status="completed",
    )
    asset.save()
    return asset


def _body(account_id, *, media=(), **override):
    return {
        "social_account_id": str(account_id),
        "caption": "Reel caption",
        "media_asset_ids": [str(m.id) for m in media],
        "platform_overrides": [{"social_account_id": str(account_id), **override}],
        "action": "draft",
    }


@pytest.fixture
def video(workspace):
    return _asset(workspace, "reel.mp4", "video")


@pytest.fixture
def image(workspace):
    return _asset(workspace, "cover.png", "image")


@pytest.mark.django_db
class TestTrialOnCreate:
    def test_trial_defaults_to_automatic_graduation(self, client_with_token, instagram_account, video):
        r = _post(client_with_token, _body(instagram_account.id, media=[video], trial=True))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {"trial": True, "trial_graduation": "SS_PERFORMANCE"}

    def test_manual_graduation_is_stored(self, client_with_token, instagram_account, video):
        r = _post(
            client_with_token,
            _body(instagram_account.id, media=[video], trial=True, trial_graduation="MANUAL"),
        )

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra["trial_graduation"] == "MANUAL"

    def test_unknown_graduation_is_rejected_by_the_schema(self, client_with_token, instagram_account, video):
        r = _post(
            client_with_token,
            _body(instagram_account.id, media=[video], trial=True, trial_graduation="AUTO"),
        )

        assert r.status_code == 422, r.content

    def test_the_response_shows_the_trial(self, client_with_token, instagram_account, video):
        r = _post(client_with_token, _body(instagram_account.id, media=[video], trial=True))
        post_id = r.json()["id"]

        got = client_with_token.get(f"/api/v1/posts/{post_id}")

        assert got.status_code == 200, got.content
        ov = got.json()["platform_overrides"][0]
        assert ov["trial"] is True
        assert ov["trial_graduation"] == "SS_PERFORMANCE"
        # The create response itself carries it too.
        assert r.json()["platform_overrides"][0]["trial"] is True

    def test_a_normal_reel_reads_back_as_null(self, client_with_token, instagram_account, video):
        r = _post(client_with_token, _body(instagram_account.id, media=[video]))

        assert r.status_code == 201, r.content
        ov = r.json()["platform_overrides"][0]
        assert ov["trial"] is None
        assert ov["trial_graduation"] is None

    def test_trial_next_to_collaborators_and_sound(self, client_with_token, instagram_account, video):
        """All extras share one dict; none may overwrite another."""
        r = _post(
            client_with_token,
            _body(
                instagram_account.id,
                media=[video],
                trial=True,
                trial_graduation="MANUAL",
                collaborators=["@autorin"],
                instagram_audio={"audio_id": "587784541076604"},
            ),
        )

        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get().platform_extra
        assert extra["trial"] is True
        assert extra["trial_graduation"] == "MANUAL"
        assert extra["collaborators"] == ["autorin"]
        assert extra["audio_id"] == "587784541076604"

    def test_trial_on_an_image_is_422(self, client_with_token, instagram_account, image):
        r = _post(client_with_token, _body(instagram_account.id, media=[image], trial=True))

        assert r.status_code == 422, r.content
        assert "exactly one video" in r.content.decode()

    def test_trial_on_a_carousel_is_422(self, client_with_token, instagram_account, workspace, video):
        second = _asset(workspace, "second.mp4", "video")
        r = _post(client_with_token, _body(instagram_account.id, media=[video, second], trial=True))

        assert r.status_code == 422, r.content
        assert "2 media item(s)" in r.content.decode()

    def test_trial_without_media_yet_is_allowed(self, client_with_token, instagram_account):
        """The video may be attached later; the publisher checks again."""
        r = _post(client_with_token, _body(instagram_account.id, trial=True))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra["trial"] is True

    def test_trial_false_stores_nothing(self, client_with_token, instagram_account, video):
        r = _post(
            client_with_token,
            _body(instagram_account.id, media=[video], trial=False, trial_graduation="MANUAL"),
        )

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {}

    def test_non_instagram_account_is_422(self, client_with_token, bluesky_account, workspace):
        clip = _asset(workspace, "bsky.mp4", "video")
        r = _post(client_with_token, _body(bluesky_account.id, media=[clip], trial=True))

        assert r.status_code == 422, r.content
        assert "only valid for Instagram" in r.content.decode()

    def test_instagram_login_account_takes_a_trial_too(self, db, user, owner_memberships, workspace, video):
        """Second way to the same account: trial_params is documented for /me/media."""
        from apps.api_keys import services
        from apps.members.models import PERMISSION_KEYS
        from apps.social_accounts.models import SocialAccount

        login_account = SocialAccount.objects.create(
            workspace=workspace,
            platform="instagram_login",
            account_platform_id="ig-login-1",
            account_name="Orbita via Instagram Login",
            connection_status="connected",
        )
        key = services.issue_api_key(
            workspace=workspace,
            social_accounts=[login_account],
            issued_by=user,
            name="trial-login",
            permissions=list(PERMISSION_KEYS),
        )
        client = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")

        r = _post(client, _body(login_account.id, media=[video], trial=True))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra["trial"] is True


@pytest.mark.django_db
class TestTrialOnUpdate:
    def _entwurf(self, client, account_id, media=()):
        r = _post(
            client,
            {
                "social_account_id": str(account_id),
                "caption": "Erst ohne",
                "media_asset_ids": [str(m.id) for m in media],
                "action": "draft",
            },
        )
        assert r.status_code == 201, r.content
        return r.json()["id"]

    def test_trial_can_be_switched_on_later(self, client_with_token, instagram_account, video):
        post_id = self._entwurf(client_with_token, instagram_account.id, media=[video])

        r = _patch(
            client_with_token,
            post_id,
            {"platform_overrides": [{"social_account_id": str(instagram_account.id), "trial": True}]},
        )

        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get().platform_extra == {"trial": True, "trial_graduation": "SS_PERFORMANCE"}

    def test_a_patch_without_the_field_keeps_it(self, client_with_token, instagram_account, video):
        r = _post(client_with_token, _body(instagram_account.id, media=[video], trial=True, trial_graduation="MANUAL"))
        post_id = r.json()["id"]

        r = _patch(client_with_token, post_id, {"internal_notes": "nur eine Notiz"})

        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get().platform_extra == {"trial": True, "trial_graduation": "MANUAL"}

    def test_collaborators_patch_keeps_the_trial(self, client_with_token, instagram_account, video):
        r = _post(client_with_token, _body(instagram_account.id, media=[video], trial=True))
        post_id = r.json()["id"]

        _patch(
            client_with_token,
            post_id,
            {"platform_overrides": [{"social_account_id": str(instagram_account.id), "collaborators": ["x"]}]},
        )

        extra = PlatformPost.objects.get().platform_extra
        assert extra["trial"] is True
        assert extra["collaborators"] == ["x"]

    def test_trial_false_removes_it(self, client_with_token, instagram_account, video):
        r = _post(client_with_token, _body(instagram_account.id, media=[video], trial=True))
        post_id = r.json()["id"]

        r = _patch(
            client_with_token,
            post_id,
            {"platform_overrides": [{"social_account_id": str(instagram_account.id), "trial": False}]},
        )

        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get().platform_extra == {}

    def test_trial_on_an_image_post_is_422(self, client_with_token, instagram_account, image):
        post_id = self._entwurf(client_with_token, instagram_account.id, media=[image])

        r = _patch(
            client_with_token,
            post_id,
            {"platform_overrides": [{"social_account_id": str(instagram_account.id), "trial": True}]},
        )

        assert r.status_code == 422, r.content
        assert PlatformPost.objects.get().platform_extra in ({}, None)
