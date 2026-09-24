"""Agent API: a channel's post as a STORY (``platform_overrides[].post_type``).

Anlass (24.09.2026): every feed post and reel gets a story about 26 hours
later. The content tool plans those stories as posts of their own; the API
only has to let it say "this one is a story".

Pinned here: ``post_type: "story"`` lands as ``platform_extra["post_type"]``
and comes back in ``platform_overrides``; only Instagram (both connection
types) and Facebook take it; exactly one image or one video (Instagram: JPEG,
video 3–60 s, at most 100 MB); carousel, text, cover, trial, collaborators
and sound are refused with 422; PATCH sets, keeps, clears and re-checks new
media; other extras survive; ``/cover`` does not put a cover on a story.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="story@example.com", password="testpass123", name="Story", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def workspace(db):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="Story Org")
    return Workspace.objects.create(name="Story WS", organization=org)


@pytest.fixture
def memberships(db, user, workspace):
    OrgMembership.objects.create(user=user, organization=workspace.organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )


def _account(workspace, platform, platform_id):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=platform_id,
        account_name=f"Orbita {platform}",
        connection_status="connected",
        oauth_access_token=f"{platform}-token",
    )


@pytest.fixture
def accounts(db, workspace):
    return {
        "instagram": _account(workspace, "instagram", "17841400000000000"),
        "instagram_login": _account(workspace, "instagram_login", "17841400000000001"),
        "facebook": _account(workspace, "facebook", "page-1"),
        "youtube": _account(workspace, "youtube", "yt-channel"),
        "bluesky": _account(workspace, "bluesky", "bsky-1"),
    }


@pytest.fixture
def client(user, memberships, workspace, accounts):
    key = services.issue_api_key(
        workspace=workspace,
        social_accounts=list(accounts.values()),
        issued_by=user,
        name="story",
        permissions=list(PERMISSION_KEYS),
    )
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


def _asset(workspace, name, media_type, *, mime=None, duration=0.0, size=32):
    from apps.media_library.models import MediaAsset

    content = ContentFile(b"\xff\xd8" + b"\x00" * 30, name=name)
    asset = MediaAsset(
        organization=workspace.organization,
        workspace=workspace,
        filename=name,
        file=content,
        media_type=media_type,
        mime_type=mime or ("video/mp4" if media_type == "video" else "image/jpeg"),
        file_size=size,
        duration=duration,
        processing_status="completed",
    )
    asset.save()
    return asset


@pytest.fixture
def video(workspace):
    return _asset(workspace, "reel.mp4", "video", duration=54.0)


@pytest.fixture
def jpeg(workspace):
    return _asset(workspace, "folie1.jpg", "image")


@pytest.fixture
def jpeg2(workspace):
    return _asset(workspace, "folie2.jpg", "image")


def _post(client, body):
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _patch(client, post_id, body):
    return client.patch(f"/api/v1/posts/{post_id}", data=json.dumps(body), content_type="application/json")


def _body(account, *, media=(), action="draft", **override):
    body = {
        "social_account_id": str(account.id),
        "caption": "E2E-Story",
        "media_asset_ids": [str(m.id) for m in media],
        "platform_overrides": [{"social_account_id": str(account.id), **override}],
        "action": action,
    }
    if action == "schedule":
        body["scheduled_at"] = (timezone.now() + timedelta(days=2)).isoformat()
    return body


def _override_out(response, account):
    return next(o for o in response.json()["platform_overrides"] if o["social_account_id"] == str(account.id))


def _extra(response):
    return PlatformPost.objects.get(post_id=response.json()["id"]).platform_extra


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStoryOnCreate:
    @pytest.mark.parametrize("platform", ["instagram", "instagram_login", "facebook"])
    def test_image_story_is_stored_and_read_back(self, client, accounts, jpeg, platform):
        acc = accounts[platform]
        r = _post(client, _body(acc, media=[jpeg], post_type="story"))
        assert r.status_code == 201, r.content
        assert _extra(r)["post_type"] == "story"
        assert _override_out(r, acc)["post_type"] == "story"
        got = client.get(f"/api/v1/posts/{r.json()['id']}")
        assert _override_out(got, acc)["post_type"] == "story"

    @pytest.mark.parametrize("platform", ["instagram", "facebook"])
    def test_video_story_can_be_scheduled(self, client, accounts, video, platform):
        acc = accounts[platform]
        r = _post(client, _body(acc, media=[video], action="schedule", post_type="story"))
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get(post_id=r.json()["id"])
        assert pp.status == "scheduled"
        assert pp.platform_extra["post_type"] == "story"

    def test_without_post_type_nothing_is_stored(self, client, accounts, jpeg):
        acc = accounts["instagram"]
        r = _post(client, _body(acc, media=[jpeg]))
        assert r.status_code == 201
        assert "post_type" not in _extra(r)
        assert _override_out(r, acc)["post_type"] is None

    @pytest.mark.parametrize("platform", ["youtube", "bluesky"])
    def test_other_platforms_are_refused(self, client, accounts, video, platform):
        r = _post(client, _body(accounts[platform], media=[video], post_type="story"))
        assert r.status_code == 422, r.content
        assert "nur für Instagram und Facebook" in r.json()["detail"]
        assert not Post.objects.filter(caption="E2E-Story").exists()

    def test_carousel_is_refused(self, client, accounts, jpeg, jpeg2):
        r = _post(client, _body(accounts["instagram"], media=[jpeg, jpeg2], post_type="story"))
        assert r.status_code == 422
        assert "2 Medien" in r.json()["detail"]

    def test_text_only_is_refused(self, client, accounts):
        r = _post(client, _body(accounts["facebook"], post_type="story"))
        assert r.status_code == 422
        assert "kein Medium" in r.json()["detail"]

    @pytest.mark.parametrize("platform", ["instagram", "instagram_login", "facebook"])
    def test_video_longer_than_60_seconds_is_refused(self, client, workspace, accounts, platform):
        long_video = _asset(workspace, "lang.mp4", "video", duration=61.2)
        r = _post(client, _body(accounts[platform], media=[long_video], post_type="story"))
        assert r.status_code == 422, r.content
        detail = r.json()["detail"]
        assert "höchstens 60 Sekunden" in detail
        assert "61.2" in detail

    def test_video_shorter_than_3_seconds_is_refused(self, client, workspace, accounts):
        short = _asset(workspace, "kurz.mp4", "video", duration=2.0)
        r = _post(client, _body(accounts["instagram"], media=[short], post_type="story"))
        assert r.status_code == 422
        assert "mindestens 3 Sekunden" in r.json()["detail"]

    def test_video_of_unknown_length_passes(self, client, workspace, accounts):
        unprocessed = _asset(workspace, "neu.mp4", "video", duration=0)
        r = _post(client, _body(accounts["instagram"], media=[unprocessed], post_type="story"))
        assert r.status_code == 201, r.content

    def test_instagram_video_over_100_mb_is_refused(self, client, workspace, accounts):
        big = _asset(workspace, "big.mp4", "video", duration=30, size=101 * 1024 * 1024)
        r = _post(client, _body(accounts["instagram"], media=[big], post_type="story"))
        assert r.status_code == 422
        assert "100 MB" in r.json()["detail"]

    def test_instagram_needs_a_jpeg_facebook_takes_png(self, client, workspace, accounts):
        png = _asset(workspace, "folie.png", "image", mime="image/png")
        r = _post(client, _body(accounts["instagram"], media=[png], post_type="story"))
        assert r.status_code == 422
        assert "JPEG" in r.json()["detail"]
        r = _post(client, _body(accounts["facebook"], media=[png], post_type="story"))
        assert r.status_code == 201, r.content

    def test_facebook_photo_over_10_mb_is_refused(self, client, workspace, accounts):
        big = _asset(workspace, "big.jpg", "image", size=11 * 1024 * 1024)
        r = _post(client, _body(accounts["facebook"], media=[big], post_type="story"))
        assert r.status_code == 422
        assert "10 MB" in r.json()["detail"]

    @pytest.mark.parametrize(
        "field",
        [
            {"cover_offset_ms": 1000},
            {"collaborators": ["partner"]},
            {"trial": True},
            {"instagram_audio": {"audio_id": "123"}},
        ],
    )
    def test_reel_settings_on_a_story_are_refused(self, client, accounts, video, field):
        r = _post(client, _body(accounts["instagram"], media=[video], post_type="story", **field))
        assert r.status_code == 422, r.content
        assert "Eine Story hat kein" in r.json()["detail"]
        assert not Post.objects.filter(caption="E2E-Story").exists()

    def test_cover_image_on_a_story_is_refused(self, client, accounts, video, jpeg):
        r = _post(client, _body(accounts["facebook"], media=[video], post_type="story", cover_asset_id=str(jpeg.id)))
        assert r.status_code == 422
        assert "Titelbild" in r.json()["detail"]

    def test_cleared_values_next_to_a_story_are_fine(self, client, accounts, video):
        r = _post(
            client,
            _body(accounts["instagram"], media=[video], post_type="story", collaborators=[], trial=False),
        )
        assert r.status_code == 201, r.content
        assert _extra(r).get("collaborators") in (None, [])

    def test_unknown_post_type_is_refused_by_the_schema(self, client, accounts, video):
        r = _post(client, _body(accounts["instagram"], media=[video], post_type="reel"))
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def _draft(workspace, account, media, *, extra=None, status="draft"):
    when = timezone.now() + timedelta(days=2)
    post = Post.objects.create(workspace=workspace, caption="E2E-Story", scheduled_at=when)
    for i, m in enumerate(media):
        PostMedia.objects.create(post=post, media_asset=m, position=i)
    PlatformPost.objects.create(
        post=post,
        social_account=account,
        status=status,
        scheduled_at=when if status == "scheduled" else None,
        platform_extra=dict(extra or {}),
    )
    return post


@pytest.mark.django_db
class TestStoryOnPatch:
    def test_patch_sets_story_and_keeps_other_extras(self, client, workspace, accounts, jpeg):
        fb = accounts["facebook"]
        post = _draft(workspace, fb, [jpeg], extra={"page_note": "bleibt"})
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(fb.id), "post_type": "story"}]})
        assert r.status_code == 200, r.content
        extra = post.platform_posts.get().platform_extra
        assert extra == {"page_note": "bleibt", "post_type": "story"}
        assert _override_out(r, fb)["post_type"] == "story"

    def test_patch_on_a_scheduled_post_keeps_date_and_status(self, client, workspace, accounts, video):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [video], status="scheduled")
        pp = post.platform_posts.get()
        before = pp.scheduled_at
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "post_type": "story"}]})
        assert r.status_code == 200, r.content
        pp.refresh_from_db()
        assert pp.status == "scheduled"
        assert pp.scheduled_at == before
        assert pp.platform_extra["post_type"] == "story"

    def test_omitted_keeps_null_clears(self, client, workspace, accounts, jpeg):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [jpeg], extra={"post_type": "story", "audio_title": "bleibt"})
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "caption": "neu"}]})
        assert r.status_code == 200
        assert post.platform_posts.get().platform_extra["post_type"] == "story"

        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "post_type": None}]})
        assert r.status_code == 200, r.content
        extra = post.platform_posts.get().platform_extra
        assert "post_type" not in extra
        assert extra["audio_title"] == "bleibt"
        assert _override_out(r, ig)["post_type"] is None

    def test_patch_story_on_a_carousel_is_refused(self, client, workspace, accounts, jpeg, jpeg2):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [jpeg, jpeg2])
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "post_type": "story"}]})
        assert r.status_code == 422
        assert "post_type" not in post.platform_posts.get().platform_extra

    def test_patch_story_with_new_single_medium_in_the_same_call(self, client, workspace, accounts, jpeg, jpeg2):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [jpeg, jpeg2])
        r = _patch(
            client,
            post.id,
            {
                "media_asset_ids": [str(jpeg.id)],
                "platform_overrides": [{"social_account_id": str(ig.id), "post_type": "story"}],
            },
        )
        assert r.status_code == 200, r.content
        assert post.platform_posts.get().platform_extra["post_type"] == "story"

    def test_second_medium_on_a_stored_story_is_refused(self, client, workspace, accounts, jpeg, jpeg2):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [jpeg], extra={"post_type": "story"})
        r = _patch(client, post.id, {"media_asset_ids": [str(jpeg.id), str(jpeg2.id)]})
        assert r.status_code == 422, r.content
        assert post.media_attachments.count() == 1

    def test_stored_cover_blocks_story_unless_cleared_in_the_same_call(self, client, workspace, accounts, video):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [video], extra={"thumb_offset_ms": 1200})
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "post_type": "story"}]})
        assert r.status_code == 422
        assert "Titelbild" in r.json()["detail"]

        r = _patch(
            client,
            post.id,
            {"platform_overrides": [{"social_account_id": str(ig.id), "post_type": "story", "cover_offset_ms": None}]},
        )
        assert r.status_code == 200, r.content
        extra = post.platform_posts.get().platform_extra
        assert extra.get("post_type") == "story"
        assert "thumb_offset_ms" not in extra

    def test_cover_on_a_stored_story_is_refused(self, client, workspace, accounts, video):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [video], extra={"post_type": "story"})
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "cover_offset_ms": 500}]})
        assert r.status_code == 422
        assert "thumb_offset_ms" not in post.platform_posts.get().platform_extra

    def test_cover_on_a_stored_image_story_names_the_story(self, client, workspace, accounts, jpeg):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [jpeg], extra={"post_type": "story"})
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(ig.id), "cover_offset_ms": 500}]})
        assert r.status_code == 422
        assert "Eine Story hat kein" in r.json()["detail"]

    def test_story_on_youtube_is_refused_on_patch(self, client, workspace, accounts, video):
        yt = accounts["youtube"]
        post = _draft(workspace, yt, [video])
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(yt.id), "post_type": "story"}]})
        assert r.status_code == 422


@pytest.mark.django_db
class TestCoverEndpointOnAStory:
    def test_explicit_story_channel_is_422(self, client, workspace, accounts, video):
        ig = accounts["instagram"]
        post = _draft(workspace, ig, [video], extra={"post_type": "story"}, status="scheduled")
        r = client.post(
            f"/api/v1/posts/{post.id}/cover",
            data=json.dumps({"social_account_id": str(ig.id), "cover_offset_ms": 100}),
            content_type="application/json",
        )
        assert r.status_code == 422
        assert "Story" in r.json()["detail"]

    def test_all_channels_skip_the_story_as_unsupported(self, client, workspace, accounts, video):
        fb = accounts["facebook"]
        post = _draft(workspace, fb, [video], extra={"post_type": "story"}, status="scheduled")
        jpeg = _asset(workspace, "cover.jpg", "image")
        r = client.post(
            f"/api/v1/posts/{post.id}/cover",
            data=json.dumps({"cover_asset_id": str(jpeg.id)}),
            content_type="application/json",
        )
        assert r.status_code == 200, r.content
        assert r.json()["results"][0]["result"] == "unsupported"
        assert "thumbnail_asset_id" not in post.platform_posts.get().platform_extra
