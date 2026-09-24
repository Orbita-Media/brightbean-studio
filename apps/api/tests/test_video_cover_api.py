"""Agent API: Titelbild (cover) of a video post.

Three ways in, one storage (``PlatformPost.platform_extra``):

* ``platform_overrides[].cover_asset_id`` / ``cover_offset_ms`` on
  ``POST /api/v1/posts`` and ``PATCH /api/v1/posts/{id}``,
* ``POST /api/v1/posts/{id}/cover`` for posts in ANY state – the content
  plan's posts are all ``scheduled`` already, where PATCH answers 409.

Pinned here: which platform takes which field (422 otherwise), the asset
must be an image of this workspace (Instagram: JPEG), the frame must lie in
the video, null removes, an omitted field keeps, other extras (sound,
collaborators, trial) survive, date and status stay untouched, published
posts go to the platform (YouTube/Facebook) or say "not possible"
(Instagram/TikTok), 409 while publishing, and the same call twice changes
nothing.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="cover@example.com", password="testpass123", name="Cover", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def workspace(db):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="Cover Org")
    return Workspace.objects.create(name="Cover WS", organization=org)


@pytest.fixture
def other_workspace(db, workspace):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Other WS", organization=workspace.organization)


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
        "facebook": _account(workspace, "facebook", "page-1"),
        "youtube": _account(workspace, "youtube", "yt-channel"),
        "tiktok": _account(workspace, "tiktok", "tt-1"),
        "bluesky": _account(workspace, "bluesky", "bsky-1"),
    }


def _client(user, workspace, accounts, permissions):
    key = services.issue_api_key(
        workspace=workspace,
        social_accounts=list(accounts.values()),
        issued_by=user,
        name="cover",
        permissions=permissions,
    )
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


@pytest.fixture
def client(user, memberships, workspace, accounts):
    return _client(user, workspace, accounts, list(PERMISSION_KEYS))


@pytest.fixture
def client_without_publish(user, memberships, workspace, accounts):
    return _client(user, workspace, accounts, [p for p in PERMISSION_KEYS if p != "publish_directly"])


def _asset(workspace, name: str, media_type: str, *, mime: str | None = None, duration: float = 0, size: int = 32):
    from apps.media_library.models import MediaAsset

    content = ContentFile(b"\xff\xd8" + b"\x00" * (size - 2), name=name)
    asset = MediaAsset(
        organization=workspace.organization,
        workspace=workspace,
        filename=name,
        file=content,
        media_type=media_type,
        mime_type=mime or ("video/mp4" if media_type == "video" else "image/jpeg"),
        file_size=content.size,
        duration=duration,
        processing_status="completed",
    )
    asset.save()
    return asset


@pytest.fixture
def video(workspace):
    return _asset(workspace, "reel.mp4", "video", duration=12.5)


@pytest.fixture
def jpeg(workspace):
    return _asset(workspace, "hook.jpg", "image")


@pytest.fixture
def png(workspace):
    return _asset(workspace, "hook.png", "image", mime="image/png")


def _post(client, body: dict):
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _patch(client, post_id, body: dict):
    return client.patch(f"/api/v1/posts/{post_id}", data=json.dumps(body), content_type="application/json")


def _cover(client, post_id, body: dict):
    return client.post(f"/api/v1/posts/{post_id}/cover", data=json.dumps(body), content_type="application/json")


def _body(account, *, media=(), **override):
    return {
        "social_account_id": str(account.id),
        "caption": "E2E-Reel",
        "media_asset_ids": [str(m.id) for m in media],
        "platform_overrides": [{"social_account_id": str(account.id), **override}],
        "action": "draft",
    }


def _override_out(response, account):
    return next(o for o in response.json()["platform_overrides"] if o["social_account_id"] == str(account.id))


def _multi_post(workspace, accounts, video, *, status="scheduled", platforms=None, extra=None):
    """A post with one child per channel, like the content plan's scheduled posts."""
    when = timezone.now() + timedelta(days=3)
    post = Post.objects.create(workspace=workspace, caption="E2E-Plan", scheduled_at=when)
    PostMedia.objects.create(post=post, media_asset=video, position=0)
    for platform in platforms or accounts:
        PlatformPost.objects.create(
            post=post,
            social_account=accounts[platform],
            status=status,
            scheduled_at=when,
            platform_extra=dict((extra or {}).get(platform, {})),
        )
    return post, when


# ---------------------------------------------------------------------------
# platform_overrides on create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCoverOnCreate:
    def test_instagram_image_and_frame_are_stored_and_read_back(self, client, accounts, video, jpeg):
        ig = accounts["instagram"]
        r = _post(client, _body(ig, media=[video], cover_asset_id=str(jpeg.id), cover_offset_ms=1500))
        assert r.status_code == 201, r.content

        pp = PlatformPost.objects.get(post_id=r.json()["id"])
        assert pp.platform_extra["thumbnail_asset_id"] == str(jpeg.id)
        assert pp.platform_extra["thumb_offset_ms"] == 1500

        got = client.get(f"/api/v1/posts/{r.json()['id']}")
        out = _override_out(got, ig)
        assert out["cover_asset_id"] == str(jpeg.id)
        assert out["cover_offset_ms"] == 1500

    def test_tiktok_frame_lands_under_both_keys(self, client, accounts, video):
        tt = accounts["tiktok"]
        r = _post(client, _body(tt, media=[video], cover_offset_ms=4000))
        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get(post_id=r.json()["id"]).platform_extra
        assert extra["thumb_offset_ms"] == 4000
        assert extra["video_cover_timestamp_ms"] == 4000
        assert _override_out(r, tt)["cover_offset_ms"] == 4000

    def test_youtube_and_facebook_take_an_image(self, client, accounts, video, png):
        for name in ("youtube", "facebook"):
            r = _post(client, _body(accounts[name], media=[video], cover_asset_id=str(png.id)))
            assert r.status_code == 201, (name, r.content)
            assert _override_out(r, accounts[name])["cover_asset_id"] == str(png.id)

    @pytest.mark.parametrize(
        ("platform", "field"),
        [
            ("youtube", "cover_offset_ms"),
            ("facebook", "cover_offset_ms"),
            ("tiktok", "cover_asset_id"),
            ("bluesky", "cover_asset_id"),
            ("bluesky", "cover_offset_ms"),
        ],
    )
    def test_field_the_platform_cannot_use_is_refused(self, client, accounts, video, jpeg, platform, field):
        value = str(jpeg.id) if field == "cover_asset_id" else 100
        r = _post(client, _body(accounts[platform], media=[video], **{field: value}))
        assert r.status_code == 422, r.content
        assert not Post.objects.filter(caption="E2E-Reel").exists()

    def test_instagram_needs_a_jpeg(self, client, accounts, video, png):
        r = _post(client, _body(accounts["instagram"], media=[video], cover_asset_id=str(png.id)))
        assert r.status_code == 422
        assert "JPEG" in r.json()["detail"]

    def test_cover_must_be_an_image_of_this_workspace(self, client, accounts, video, other_workspace):
        foreign = _asset(other_workspace, "foreign.jpg", "image")
        r = _post(client, _body(accounts["instagram"], media=[video], cover_asset_id=str(foreign.id)))
        assert r.status_code == 422
        r = _post(client, _body(accounts["instagram"], media=[video], cover_asset_id=str(video.id)))
        assert r.status_code == 422
        assert "image" in r.json()["detail"]

    def test_image_post_gets_no_cover(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["instagram"], media=[jpeg], cover_offset_ms=10))
        assert r.status_code == 422
        assert "Video" in r.json()["detail"]

    def test_frame_must_lie_inside_the_video(self, client, accounts, video):
        r = _post(client, _body(accounts["instagram"], media=[video], cover_offset_ms=12_501))
        assert r.status_code == 422
        r = _post(client, _body(accounts["instagram"], media=[video], cover_offset_ms=12_500))
        assert r.status_code == 201, r.content

    def test_negative_offset_is_refused(self, client, accounts, video):
        r = _post(client, _body(accounts["tiktok"], media=[video], cover_offset_ms=-1))
        assert r.status_code == 422

    def test_null_on_create_stores_nothing(self, client, accounts, video):
        r = _post(client, _body(accounts["instagram"], media=[video], cover_asset_id=None, cover_offset_ms=None))
        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get(post_id=r.json()["id"]).platform_extra
        assert "thumbnail_asset_id" not in extra
        assert "thumb_offset_ms" not in extra
        out = _override_out(r, accounts["instagram"])
        assert out["cover_asset_id"] is None
        assert out["cover_offset_ms"] is None

    def test_cover_sits_next_to_sound_collaborators_and_trial(self, client, accounts, video, jpeg):
        r = _post(
            client,
            _body(
                accounts["instagram"],
                media=[video],
                instagram_audio={"audio_id": "aud-1"},
                collaborators=["partner"],
                trial=True,
                cover_asset_id=str(jpeg.id),
            ),
        )
        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get(post_id=r.json()["id"]).platform_extra
        assert extra["audio_id"] == "aud-1"
        assert extra["collaborators"] == ["partner"]
        assert extra["trial"] is True
        assert extra["thumbnail_asset_id"] == str(jpeg.id)


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCoverOnPatch:
    def _draft(self, client, accounts, video, **override):
        r = _post(client, _body(accounts["instagram"], media=[video], **override))
        assert r.status_code == 201, r.content
        return r.json()["id"]

    def test_patch_sets_and_keeps_other_extras(self, client, accounts, video, jpeg):
        post_id = self._draft(client, accounts, video, collaborators=["partner"], trial=True)
        ig = accounts["instagram"]
        r = _patch(
            client,
            post_id,
            {"platform_overrides": [{"social_account_id": str(ig.id), "cover_asset_id": str(jpeg.id)}]},
        )
        assert r.status_code == 200, r.content
        extra = PlatformPost.objects.get(post_id=post_id).platform_extra
        assert extra["thumbnail_asset_id"] == str(jpeg.id)
        assert extra["collaborators"] == ["partner"]
        assert extra["trial"] is True

    def test_omitted_field_keeps_null_removes(self, client, accounts, video, jpeg):
        post_id = self._draft(client, accounts, video, cover_asset_id=str(jpeg.id), cover_offset_ms=900)
        ig = accounts["instagram"]

        r = _patch(client, post_id, {"platform_overrides": [{"social_account_id": str(ig.id), "caption": "neu"}]})
        assert r.status_code == 200, r.content
        out = _override_out(r, ig)
        assert (out["cover_asset_id"], out["cover_offset_ms"]) == (str(jpeg.id), 900)

        r = _patch(client, post_id, {"platform_overrides": [{"social_account_id": str(ig.id), "cover_asset_id": None}]})
        assert r.status_code == 200, r.content
        out = _override_out(r, ig)
        assert (out["cover_asset_id"], out["cover_offset_ms"]) == (None, 900)

        r = _patch(
            client, post_id, {"platform_overrides": [{"social_account_id": str(ig.id), "cover_offset_ms": None}]}
        )
        extra = PlatformPost.objects.get(post_id=post_id).platform_extra
        assert "thumbnail_asset_id" not in extra
        assert "thumb_offset_ms" not in extra

    def test_patch_refuses_wrong_platform_field(self, client, accounts, video):
        r = _post(client, _body(accounts["youtube"], media=[video]))
        yt = accounts["youtube"]
        r = _patch(
            client,
            r.json()["id"],
            {"platform_overrides": [{"social_account_id": str(yt.id), "cover_offset_ms": 100}]},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST /posts/{id}/cover
# ---------------------------------------------------------------------------


def _by_platform(response):
    return {item["platform"]: item for item in response.json()["results"]}


@pytest.mark.django_db
class TestCoverEndpoint:
    def test_scheduled_post_all_channels_saved_date_and_status_untouched(
        self, client, workspace, accounts, video, jpeg
    ):
        post, when = _multi_post(workspace, accounts, video, extra={"tiktok": {"privacy_level": "SELF_ONLY"}})

        r = _cover(client, post.id, {"cover_asset_id": str(jpeg.id), "cover_offset_ms": 2000})
        assert r.status_code == 200, r.content
        res = _by_platform(r)
        assert set(res) == {"instagram", "facebook", "youtube", "tiktok", "bluesky"}
        for platform in ("instagram", "facebook", "youtube", "tiktok"):
            assert res[platform]["result"] == "saved", res[platform]
            assert res[platform]["status"] == "scheduled"
        assert res["bluesky"]["result"] == "unsupported"
        assert "Bluesky" in res["bluesky"]["message"]
        assert "Nicht übernommen" in res["tiktok"]["message"]  # the image part does not fit TikTok

        kids = {pp.social_account.platform: pp for pp in post.platform_posts.select_related("social_account")}
        assert kids["instagram"].platform_extra == {"thumbnail_asset_id": str(jpeg.id), "thumb_offset_ms": 2000}
        assert kids["youtube"].platform_extra == {"thumbnail_asset_id": str(jpeg.id)}
        assert kids["facebook"].platform_extra == {"thumbnail_asset_id": str(jpeg.id)}
        assert kids["tiktok"].platform_extra == {
            "privacy_level": "SELF_ONLY",
            "thumb_offset_ms": 2000,
            "video_cover_timestamp_ms": 2000,
        }
        assert kids["bluesky"].platform_extra == {}
        for pp in kids.values():
            assert pp.status == "scheduled"
            assert pp.scheduled_at == when

        again = _cover(client, post.id, {"cover_asset_id": str(jpeg.id), "cover_offset_ms": 2000})
        assert again.status_code == 200
        res2 = _by_platform(again)
        for platform in ("instagram", "facebook", "youtube", "tiktok"):
            assert res2[platform]["result"] == "unchanged", res2[platform]
        assert res2["bluesky"]["result"] == "unsupported"

    def test_one_channel_only(self, client, workspace, accounts, video):
        post, _ = _multi_post(workspace, accounts, video, platforms=["instagram", "tiktok"])
        r = _cover(client, post.id, {"social_account_id": str(accounts["tiktok"].id), "cover_offset_ms": 700})
        assert r.status_code == 200, r.content
        assert [i["platform"] for i in r.json()["results"]] == ["tiktok"]
        assert post.platform_posts.get(social_account=accounts["instagram"]).platform_extra == {}

    def test_explicit_channel_with_unfit_field_is_422(self, client, workspace, accounts, video, jpeg):
        post, _ = _multi_post(workspace, accounts, video, platforms=["tiktok"])
        r = _cover(client, post.id, {"social_account_id": str(accounts["tiktok"].id), "cover_asset_id": str(jpeg.id)})
        assert r.status_code == 422

    def test_unknown_channel_is_422(self, client, workspace, accounts, video):
        post, _ = _multi_post(workspace, accounts, video, platforms=["tiktok"])
        r = _cover(client, post.id, {"social_account_id": str(accounts["youtube"].id), "cover_offset_ms": 1})
        assert r.status_code == 422

    def test_empty_request_is_422(self, client, workspace, accounts, video):
        post, _ = _multi_post(workspace, accounts, video, platforms=["tiktok"])
        assert _cover(client, post.id, {}).status_code == 422
        assert _cover(client, post.id, {"social_account_id": str(accounts["tiktok"].id)}).status_code == 422

    def test_null_removes_on_a_scheduled_post(self, client, workspace, accounts, video, jpeg):
        post, _ = _multi_post(
            workspace,
            accounts,
            video,
            platforms=["instagram"],
            extra={"instagram": {"thumbnail_asset_id": str(jpeg.id), "thumb_offset_ms": 5, "audio_id": "a"}},
        )
        r = _cover(client, post.id, {"cover_asset_id": None})
        assert _by_platform(r)["instagram"]["result"] == "saved"
        assert post.platform_posts.get().platform_extra == {"thumb_offset_ms": 5, "audio_id": "a"}

    def test_png_for_instagram_is_an_error_there_but_saved_elsewhere(self, client, workspace, accounts, video, png):
        post, _ = _multi_post(workspace, accounts, video, platforms=["instagram", "youtube"])
        res = _by_platform(_cover(client, post.id, {"cover_asset_id": str(png.id)}))
        assert res["instagram"]["result"] == "error"
        assert "JPEG" in res["instagram"]["message"]
        assert res["youtube"]["result"] == "saved"
        assert post.platform_posts.get(social_account=accounts["instagram"]).platform_extra == {}

    def test_publishing_channel_is_409_and_nothing_changes(self, client, workspace, accounts, video):
        post, _ = _multi_post(workspace, accounts, video, platforms=["instagram", "tiktok"])
        post.platform_posts.filter(social_account=accounts["tiktok"]).update(status="publishing")
        r = _cover(client, post.id, {"cover_offset_ms": 300})
        assert r.status_code == 409
        assert post.platform_posts.get(social_account=accounts["instagram"]).platform_extra == {}

    def test_image_only_post_is_422(self, client, workspace, accounts, jpeg):
        post, _ = _multi_post(workspace, accounts, jpeg, platforms=["instagram"])
        assert _cover(client, post.id, {"cover_offset_ms": 1}).status_code == 422

    # ---- published

    def _published(self, workspace, accounts, video, platform, **extra):
        post, _ = _multi_post(
            workspace, accounts, video, platforms=[platform], status="published", extra={platform: extra}
        )
        pp = post.platform_posts.get()
        pp.platform_post_id = {"youtube": "yt-video-1", "facebook": "post-1"}.get(platform, "media-1")
        pp.published_at = timezone.now()
        pp.save()
        return post, pp

    @pytest.mark.parametrize("platform", ["instagram", "tiktok"])
    def test_published_instagram_and_tiktok_say_not_possible(self, client, workspace, accounts, video, jpeg, platform):
        post, pp = self._published(workspace, accounts, video, platform)
        body = {"cover_offset_ms": 100} if platform == "tiktok" else {"cover_asset_id": str(jpeg.id)}
        with patch("providers.get_provider") as get_provider:
            r = _cover(client, post.id, body)
        assert r.status_code == 200, r.content
        item = r.json()["results"][0]
        assert item["result"] == "unsupported"
        assert "nach dem Veröffentlichen nicht änderbar" in item["message"]
        get_provider.assert_not_called()
        pp.refresh_from_db()
        assert pp.platform_extra == {}

    def test_published_youtube_is_updated_once(self, client, workspace, accounts, video, jpeg):
        post, pp = self._published(workspace, accounts, video, "youtube")
        provider = MagicMock()
        with patch("providers.get_provider", return_value=provider):
            r = _cover(client, post.id, {"cover_asset_id": str(jpeg.id)})
            again = _cover(client, post.id, {"cover_asset_id": str(jpeg.id)})

        assert r.json()["results"][0]["result"] == "updated", r.content
        assert again.json()["results"][0]["result"] == "unchanged"
        provider.set_video_thumbnail.assert_called_once()
        token, video_id, path = provider.set_video_thumbnail.call_args.args
        assert (token, video_id) == ("youtube-token", "yt-video-1")
        assert path.endswith(".jpg")
        pp.refresh_from_db()
        assert pp.platform_extra["thumbnail_asset_id"] == str(jpeg.id)
        assert pp.platform_extra["thumbnail_set"] is True
        assert pp.status == "published"

    def test_published_facebook_uses_the_video_id(self, client, workspace, accounts, video, jpeg):
        post, pp = self._published(
            workspace, accounts, video, "facebook", video_id="fb-video-9", thumbnail_set=False, thumbnail_error="x"
        )
        provider = MagicMock()
        with patch("providers.get_provider", return_value=provider):
            r = _cover(client, post.id, {"cover_asset_id": str(jpeg.id)})
        assert r.json()["results"][0]["result"] == "updated", r.content
        assert provider.set_video_thumbnail.call_args.args[1] == "fb-video-9"
        pp.refresh_from_db()
        assert pp.platform_extra["thumbnail_set"] is True
        assert "thumbnail_error" not in pp.platform_extra

    def test_published_provider_error_is_reported_and_nothing_stored(self, client, workspace, accounts, video, jpeg):
        post, pp = self._published(workspace, accounts, video, "youtube")
        provider = MagicMock()
        provider.set_video_thumbnail.side_effect = RuntimeError("quota exceeded")
        with patch("providers.get_provider", return_value=provider):
            r = _cover(client, post.id, {"cover_asset_id": str(jpeg.id)})
        item = r.json()["results"][0]
        assert item["result"] == "error"
        assert "quota exceeded" in item["message"]
        pp.refresh_from_db()
        assert pp.platform_extra == {}

    def test_published_cover_cannot_be_removed(self, client, workspace, accounts, video, jpeg):
        post, _ = self._published(workspace, accounts, video, "youtube", thumbnail_asset_id=str(jpeg.id))
        r = _cover(client, post.id, {"cover_asset_id": None})
        assert r.json()["results"][0]["result"] == "unsupported"

    def test_changing_a_live_video_needs_publish_directly(
        self, client_without_publish, workspace, accounts, video, jpeg
    ):
        post, _ = self._published(workspace, accounts, video, "youtube")
        r = _cover(client_without_publish, post.id, {"cover_asset_id": str(jpeg.id)})
        assert r.status_code == 403

    def test_scheduled_post_needs_only_create_posts(self, client_without_publish, workspace, accounts, video, jpeg):
        post, _ = _multi_post(workspace, accounts, video, platforms=["youtube"])
        r = _cover(client_without_publish, post.id, {"cover_asset_id": str(jpeg.id)})
        assert r.status_code == 200, r.content
        assert r.json()["results"][0]["result"] == "saved"


@pytest.mark.django_db
def test_openapi_documents_the_cover_fields(client):
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200, r.status_code
    spec = r.json()
    assert "/api/v1/posts/{post_id}/cover" in spec["paths"]
    schemas = spec["components"]["schemas"]
    assert {"cover_asset_id", "cover_offset_ms"} <= set(schemas["PlatformOverride"]["properties"])
    assert {"cover_asset_id", "cover_offset_ms"} <= set(schemas["PlatformOverrideOut"]["properties"])
    result_enum = json.dumps(schemas["CoverChannelResult"])
    for value in ("saved", "updated", "unchanged", "unsupported", "error"):
        assert value in result_enum
