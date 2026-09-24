"""Agent API: Pinterest board, link and video-pin cover.

Anlass (24.09.2026): ``providers/pinterest.py`` needs ``board_id``, but the
API could set neither ``board_id`` nor ``link_url`` – both scheduled pins had
``platform_extra == {}`` and would have failed at their publish time.

Pinned here: ``board_id`` / ``link_url`` per pin on POST and PATCH (also for
scheduled pins), read back in ``platform_overrides``; https only; Pinterest
only; scheduling without a board (own or account default) is 422 – on create,
on ``/schedule`` and when PATCH clears it; a video pin needs a cover
(``cover_asset_id`` → ``cover_image_asset_id`` or ``cover_offset_ms``); the
board list and the default board of an account.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership

BOARD = "1082834372847314271"
BOARDS = [
    {"id": BOARD, "name": "Soziales", "privacy": "PUBLIC"},
    {"id": "1082834372847314999", "name": "Bücher", "privacy": "SECRET"},
]


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="pin@example.com", password="testpass123", name="Pin", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def workspace(db):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="Pin Org")
    return Workspace.objects.create(name="Pin WS", organization=org)


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
        "pinterest": _account(workspace, "pinterest", "1082834441564871417"),
        "instagram": _account(workspace, "instagram", "17841400000000000"),
    }


def _client(user, workspace, accounts, permissions):
    key = services.issue_api_key(
        workspace=workspace,
        social_accounts=list(accounts.values()),
        issued_by=user,
        name="pin",
        permissions=permissions,
    )
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


@pytest.fixture
def client(user, memberships, workspace, accounts):
    return _client(user, workspace, accounts, list(PERMISSION_KEYS))


@pytest.fixture
def client_without_accounts_perm(user, memberships, workspace, accounts):
    return _client(user, workspace, accounts, [p for p in PERMISSION_KEYS if p != "manage_social_accounts"])


def _asset(workspace, name, media_type, *, mime=None, duration=0.0):
    from apps.media_library.models import MediaAsset

    asset = MediaAsset(
        organization=workspace.organization,
        workspace=workspace,
        filename=name,
        file=ContentFile(b"\xff\xd8" + b"\x00" * 30, name=name),
        media_type=media_type,
        mime_type=mime or ("video/mp4" if media_type == "video" else "image/jpeg"),
        file_size=32,
        duration=duration,
        processing_status="completed",
    )
    asset.save()
    return asset


@pytest.fixture
def jpeg(workspace):
    return _asset(workspace, "pin.jpg", "image")


@pytest.fixture
def video(workspace):
    return _asset(workspace, "pin.mp4", "video", duration=30)


def _post(client, body):
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _patch(client, post_id, body):
    return client.patch(f"/api/v1/posts/{post_id}", data=json.dumps(body), content_type="application/json")


def _body(account, *, media=(), action="draft", **override):
    body = {
        "social_account_id": str(account.id),
        "caption": "E2E-Pin",
        "title": "E2E-Pin",
        "media_asset_ids": [str(m.id) for m in media],
        "action": action,
    }
    if override:
        body["platform_overrides"] = [{"social_account_id": str(account.id), **override}]
    if action == "schedule":
        body["scheduled_at"] = (timezone.now() + timedelta(days=4)).isoformat()
    return body


def _out(response, account):
    return next(o for o in response.json()["platform_overrides"] if o["social_account_id"] == str(account.id))


def _set_default(account, board=BOARD):
    from apps.social_accounts.pinterest import set_default_board

    set_default_board(account, board, "Soziales")


# ---------------------------------------------------------------------------
# board_id / link_url on create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPinFieldsOnCreate:
    def test_board_and_link_are_stored_and_read_back(self, client, accounts, jpeg):
        pin = accounts["pinterest"]
        r = _post(client, _body(pin, media=[jpeg], board_id=BOARD, link_url="https://orbita-media.de/buch"))
        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get(post_id=r.json()["id"]).platform_extra
        assert extra["board_id"] == BOARD
        assert extra["link_url"] == "https://orbita-media.de/buch"
        out = _out(client.get(f"/api/v1/posts/{r.json()['id']}"), pin)
        assert out["board_id"] == BOARD
        assert out["link_url"] == "https://orbita-media.de/buch"

    def test_fields_are_pinterest_only(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["instagram"], media=[jpeg], board_id=BOARD))
        assert r.status_code == 422
        assert "nur bei Pinterest" in r.json()["detail"]
        r = _post(client, _body(accounts["instagram"], media=[jpeg], link_url="https://orbita-media.de"))
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "link", ["http://orbita-media.de", "https://", "https://orbita media.de", "ftp://orbita-media.de", "orbita"]
    )
    def test_link_must_be_https(self, client, accounts, jpeg, link):
        r = _post(client, _body(accounts["pinterest"], media=[jpeg], board_id=BOARD, link_url=link))
        assert r.status_code == 422, link
        assert not Post.objects.filter(caption="E2E-Pin").exists()

    def test_link_longer_than_2048_is_refused(self, client, accounts, jpeg):
        link = "https://orbita-media.de/" + "a" * 2030
        r = _post(client, _body(accounts["pinterest"], media=[jpeg], board_id=BOARD, link_url=link))
        assert r.status_code == 422

    def test_board_id_must_be_numeric(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["pinterest"], media=[jpeg], board_id="soziales"))
        assert r.status_code == 422

    def test_schedule_without_any_board_is_refused_before_the_date(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["pinterest"], media=[jpeg], action="schedule"))
        assert r.status_code == 422, r.content
        assert "Kein Board" in r.json()["detail"]
        assert not Post.objects.filter(caption="E2E-Pin").exists()

    def test_a_draft_may_stay_without_a_board(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["pinterest"], media=[jpeg]))
        assert r.status_code == 201

    def test_schedule_with_own_board_or_account_default(self, client, accounts, jpeg):
        pin = accounts["pinterest"]
        r = _post(client, _body(pin, media=[jpeg], action="schedule", board_id=BOARD))
        assert r.status_code == 201, r.content
        _set_default(pin)
        r = _post(client, _body(pin, media=[jpeg], action="schedule"))
        assert r.status_code == 201, r.content
        # The default is not copied into the pin: changing it later still applies.
        assert _out(r, pin)["board_id"] is None

    def test_video_pin_needs_a_cover_to_be_scheduled(self, client, accounts, video, jpeg):
        pin = accounts["pinterest"]
        r = _post(client, _body(pin, media=[video], action="schedule", board_id=BOARD))
        assert r.status_code == 422
        assert "Titelbild" in r.json()["detail"]

        r = _post(client, _body(pin, media=[video], action="schedule", board_id=BOARD, cover_asset_id=str(jpeg.id)))
        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get(post_id=r.json()["id"]).platform_extra
        assert extra["cover_image_asset_id"] == str(jpeg.id)
        assert "thumbnail_asset_id" not in extra
        assert _out(r, pin)["cover_asset_id"] == str(jpeg.id)

        r = _post(client, _body(pin, media=[video], action="schedule", board_id=BOARD, cover_offset_ms=2000))
        assert r.status_code == 201, r.content
        assert _out(r, pin)["cover_offset_ms"] == 2000

    def test_pinterest_cover_must_be_jpeg_or_png(self, client, workspace, accounts, video):
        webp = _asset(workspace, "c.webp", "image", mime="image/webp")
        r = _post(client, _body(accounts["pinterest"], media=[video], board_id=BOARD, cover_asset_id=str(webp.id)))
        assert r.status_code == 422
        assert "JPEG oder PNG" in r.json()["detail"]

    def test_image_pin_gets_no_cover(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["pinterest"], media=[jpeg], board_id=BOARD, cover_asset_id=str(jpeg.id)))
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PATCH and /schedule
# ---------------------------------------------------------------------------


def _pin_post(workspace, account, media, *, status="scheduled", extra=None):
    when = timezone.now() + timedelta(days=4)
    post = Post.objects.create(workspace=workspace, caption="E2E-Pin", scheduled_at=when)
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
class TestPinFieldsOnPatch:
    def test_scheduled_pin_gets_board_and_link_date_and_status_untouched(self, client, workspace, accounts, jpeg):
        pin = accounts["pinterest"]
        post = _pin_post(workspace, pin, [jpeg])
        pp = post.platform_posts.get()
        before = pp.scheduled_at
        r = _patch(
            client,
            post.id,
            {
                "platform_overrides": [
                    {"social_account_id": str(pin.id), "board_id": BOARD, "link_url": "https://orbita-media.de/x"}
                ]
            },
        )
        assert r.status_code == 200, r.content
        pp.refresh_from_db()
        assert pp.status == "scheduled"
        assert pp.scheduled_at == before
        assert pp.platform_extra == {"board_id": BOARD, "link_url": "https://orbita-media.de/x"}

    def test_omitted_keeps_null_removes(self, client, workspace, accounts, jpeg):
        pin = accounts["pinterest"]
        post = _pin_post(
            workspace,
            pin,
            [jpeg],
            status="draft",
            extra={"board_id": BOARD, "link_url": "https://a.de", "alt_text": "x"},
        )
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(pin.id), "caption": "neu"}]})
        assert r.status_code == 200
        assert post.platform_posts.get().platform_extra["board_id"] == BOARD
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(pin.id), "link_url": None}]})
        assert r.status_code == 200
        assert post.platform_posts.get().platform_extra == {"board_id": BOARD, "alt_text": "x"}

    def test_clearing_the_board_of_a_scheduled_pin_needs_a_default(self, client, workspace, accounts, jpeg):
        pin = accounts["pinterest"]
        post = _pin_post(workspace, pin, [jpeg], extra={"board_id": BOARD})
        body = {"platform_overrides": [{"social_account_id": str(pin.id), "board_id": None}]}
        r = _patch(client, post.id, body)
        assert r.status_code == 422
        assert post.platform_posts.get().platform_extra == {"board_id": BOARD}
        _set_default(pin)
        r = _patch(client, post.id, body)
        assert r.status_code == 200, r.content
        assert "board_id" not in post.platform_posts.get().platform_extra

    def test_schedule_route_refuses_a_pin_without_board(self, client, workspace, accounts, jpeg):
        pin = accounts["pinterest"]
        post = _pin_post(workspace, pin, [jpeg], status="draft")
        when = (timezone.now() + timedelta(days=5)).isoformat()
        r = client.post(
            f"/api/v1/posts/{post.id}/schedule",
            data=json.dumps({"scheduled_at": when}),
            content_type="application/json",
        )
        assert r.status_code == 422
        assert post.platform_posts.get().status == "draft"
        _set_default(pin)
        r = client.post(
            f"/api/v1/posts/{post.id}/schedule",
            data=json.dumps({"scheduled_at": when}),
            content_type="application/json",
        )
        assert r.status_code == 200, r.content
        assert post.platform_posts.get().status == "scheduled"


# ---------------------------------------------------------------------------
# Accounts: board list and default board
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPinterestBoardsEndpoints:
    def test_board_list(self, client, accounts):
        pin = accounts["pinterest"]
        _set_default(pin)
        with patch("apps.social_accounts.pinterest.fetch_boards", return_value=BOARDS):
            r = client.get(f"/api/v1/accounts/{pin.id}/pinterest-boards")
        assert r.status_code == 200, r.content
        assert r.json() == {"boards": BOARDS, "default_board_id": BOARD}

    def test_board_list_only_for_pinterest(self, client, accounts):
        r = client.get(f"/api/v1/accounts/{accounts['instagram'].id}/pinterest-boards")
        assert r.status_code == 422

    def test_board_list_foreign_account_is_403(self, client, workspace):
        other = _account(workspace, "pinterest", "999")
        r = client.get(f"/api/v1/accounts/{other.id}/pinterest-boards")
        assert r.status_code == 403

    def test_board_list_upstream_error_is_502(self, client, accounts):
        with patch("apps.social_accounts.pinterest.fetch_boards", side_effect=RuntimeError("boom")):
            r = client.get(f"/api/v1/accounts/{accounts['pinterest'].id}/pinterest-boards")
        assert r.status_code == 502

    def test_set_and_remove_default_board(self, client, accounts):
        pin = accounts["pinterest"]
        url = f"/api/v1/accounts/{pin.id}/pinterest-default-board"
        with patch("apps.social_accounts.pinterest.fetch_boards", return_value=BOARDS):
            r = client.put(url, data=json.dumps({"board_id": BOARD}), content_type="application/json")
        assert r.status_code == 200, r.content
        assert r.json()["pinterest_default_board_id"] == BOARD
        assert r.json()["pinterest_default_board_name"] == "Soziales"
        pin.refresh_from_db()
        assert pin.platform_settings["pinterest_default_board_id"] == BOARD

        listed = client.get("/api/v1/accounts/").json()["accounts"]
        assert next(a for a in listed if a["id"] == str(pin.id))["pinterest_default_board_id"] == BOARD

        r = client.put(url, data=json.dumps({"board_id": None}), content_type="application/json")
        assert r.status_code == 200
        assert r.json()["pinterest_default_board_id"] is None

    def test_unknown_board_is_refused(self, client, accounts):
        pin = accounts["pinterest"]
        with patch("apps.social_accounts.pinterest.fetch_boards", return_value=BOARDS):
            r = client.put(
                f"/api/v1/accounts/{pin.id}/pinterest-default-board",
                data=json.dumps({"board_id": "123"}),
                content_type="application/json",
            )
        assert r.status_code == 422
        pin.refresh_from_db()
        assert pin.platform_settings == {}

    def test_default_board_needs_manage_social_accounts(self, client_without_accounts_perm, accounts):
        pin = accounts["pinterest"]
        r = client_without_accounts_perm.put(
            f"/api/v1/accounts/{pin.id}/pinterest-default-board",
            data=json.dumps({"board_id": BOARD}),
            content_type="application/json",
        )
        assert r.status_code == 403
