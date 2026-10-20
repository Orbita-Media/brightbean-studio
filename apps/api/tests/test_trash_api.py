"""Trash over the agent API: DELETE moves to the trash, restore brings back,
``?permanent=true`` deletes for good only from the trash.

Posts: scheduled children go back to draft so nothing publishes from the
trash; a trashed post vanishes from reads and from the PlatformPost default
manager (calendar, publisher), but still counts on the platform quota.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.api.tests.test_media_router import (  # noqa: F401  (fixtures)
    _png,
    _SecureClient,
    client_with_token,
    issued_key,
    organization,
    other_workspace,
    owner_memberships,
    social_account,
    user,
    workspace,
)
from apps.api_keys import services
from apps.members.models import PERMISSION_KEYS


def _upload(client, name="hero.png"):
    r = client.post("/api/v1/media/", data={"file": _png(name=name)})
    assert r.status_code == 201, r.content
    return r.json()["id"]


@pytest.mark.django_db
class TestMediaTrash:
    def test_delete_moves_to_trash_and_restore_brings_back(self, client_with_token):
        from apps.media_library.models import MediaAsset

        mid = _upload(client_with_token)
        r = client_with_token.delete(f"/api/v1/media/{mid}")
        assert r.status_code == 200, r.content
        assert r.json()["deleted_at"]
        # Hidden from reads and lists, file row still there.
        assert client_with_token.get(f"/api/v1/media/{mid}").status_code == 404
        assert client_with_token.get("/api/v1/media/?processing_status=any").json()["items"] == []
        assert MediaAsset.all_objects.filter(id=mid).exists()
        trash = client_with_token.get("/api/v1/media/?trashed=true&processing_status=any").json()["items"]
        assert [a["id"] for a in trash] == [mid]

        r = client_with_token.post(f"/api/v1/media/{mid}/restore")
        assert r.status_code == 200, r.content
        assert r.json()["deleted_at"] is None
        assert client_with_token.get(f"/api/v1/media/{mid}").status_code == 200

    def test_permanent_only_from_trash(self, client_with_token):
        from apps.media_library.models import MediaAsset

        mid = _upload(client_with_token)
        r = client_with_token.delete(f"/api/v1/media/{mid}?permanent=true")
        assert r.status_code == 409
        assert MediaAsset.objects.filter(id=mid).exists()

        assert client_with_token.delete(f"/api/v1/media/{mid}").status_code == 200
        r = client_with_token.delete(f"/api/v1/media/{mid}?permanent=true")
        assert r.status_code == 204
        assert not MediaAsset.all_objects.filter(id=mid).exists()

    def test_needs_delete_media(self, db, user, owner_memberships, workspace, social_account):
        key = services.issue_api_key(
            workspace=workspace,
            social_accounts=[social_account],
            issued_by=user,
            name="no-delete",
            permissions=[p for p in PERMISSION_KEYS if p != "delete_media"],
        )
        c = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")
        mid = _upload(c)
        r = c.delete(f"/api/v1/media/{mid}")
        assert r.status_code == 403
        assert "delete_media" in r.json()["detail"]

    def test_used_by_scheduled_post_is_blocked(self, client_with_token, workspace, social_account):
        from apps.composer.models import PlatformPost, Post, PostMedia
        from apps.media_library.models import MediaAsset

        mid = _upload(client_with_token)
        post = Post.objects.create(workspace=workspace, caption="soon")
        PlatformPost.objects.create(
            post=post, social_account=social_account, status="scheduled",
            scheduled_at=timezone.now() + dt.timedelta(days=1),
        )
        PostMedia.objects.create(post=post, media_asset=MediaAsset.objects.get(id=mid), position=0)
        r = client_with_token.delete(f"/api/v1/media/{mid}")
        assert r.status_code == 409
        assert MediaAsset.objects.filter(id=mid).exists()


@pytest.mark.django_db
class TestPostTrash:
    def _post(self, workspace, social_account, status="scheduled"):
        from apps.composer.models import PlatformPost, Post

        post = Post.objects.create(workspace=workspace, caption="trash me")
        PlatformPost.objects.create(
            post=post, social_account=social_account, status=status,
            scheduled_at=timezone.now() + dt.timedelta(days=1) if status == "scheduled" else None,
        )
        return post

    def test_trash_unschedules_hides_and_restores(self, client_with_token, workspace, social_account):
        from apps.composer.models import PlatformPost, Post

        post = self._post(workspace, social_account)
        r = client_with_token.delete(f"/api/v1/posts/{post.id}")
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["deleted_at"]
        assert [pp["status"] for pp in body["platform_posts"]] == ["draft"]

        assert client_with_token.get(f"/api/v1/posts/{post.id}").status_code == 404
        assert not Post.objects.filter(id=post.id).exists()
        assert not PlatformPost.objects.filter(post_id=post.id).exists()
        assert PlatformPost.all_objects.get(post_id=post.id).status == "draft"
        assert [p["id"] for p in client_with_token.get("/api/v1/posts/trash").json()] == [str(post.id)]

        r = client_with_token.post(f"/api/v1/posts/{post.id}/restore")
        assert r.status_code == 200, r.content
        assert r.json()["deleted_at"] is None
        assert client_with_token.get(f"/api/v1/posts/{post.id}").status_code == 200

    def test_permanent_only_from_trash(self, client_with_token, workspace, social_account):
        from apps.composer.models import PlatformPost, Post

        post = self._post(workspace, social_account, status="draft")
        assert client_with_token.delete(f"/api/v1/posts/{post.id}?permanent=true").status_code == 409
        assert client_with_token.delete(f"/api/v1/posts/{post.id}").status_code == 200
        assert client_with_token.delete(f"/api/v1/posts/{post.id}?permanent=true").status_code == 204
        assert not Post.all_objects.filter(id=post.id).exists()
        assert not PlatformPost.all_objects.filter(post_id=post.id).exists()

    def test_publishing_blocks_trash(self, client_with_token, workspace, social_account):
        post = self._post(workspace, social_account, status="publishing")
        assert client_with_token.delete(f"/api/v1/posts/{post.id}").status_code == 409

    def test_trashed_published_post_still_counts_on_quota(self, client_with_token, workspace, social_account):
        from apps.api.limits import publish_times
        from apps.composer.models import PlatformPost

        post = self._post(workspace, social_account, status="draft")
        PlatformPost.objects.filter(post=post).update(status="published", published_at=timezone.now())
        assert client_with_token.delete(f"/api/v1/posts/{post.id}").status_code == 200
        now = timezone.now()
        times = publish_times(social_account, start=now - dt.timedelta(hours=1), end=now + dt.timedelta(hours=1))
        assert len(times) == 1
