"""Agent API side of the Instagram reel sound.

Two routes:

* ``GET /api/v1/accounts/{id}/instagram-audio`` finds a track (no query =
  Meta's trending list),
* ``POST /api/v1/posts`` attaches it through
  ``platform_overrides[].instagram_audio``, which lands in
  ``PlatformPost.platform_extra`` and reaches the provider through the same
  path as every other platform-specific setting.

The publishing tool composes its posts over this API, so a reel with a
platform sound has to be creatable without a human in the composer.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.composer.models import PlatformPost
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="reel-sound@example.com",
        password="testpass123",
        name="Reel Sound",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Sound Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Sound WS", organization=organization)


@pytest.fixture
def owner_memberships(db, user, organization, workspace):
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )


@pytest.fixture
def instagram_account(db, workspace):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="17841400000000000",
        account_name="Orbita Media",
        connection_status="connected",
        oauth_access_token="page-token",
    )


@pytest.fixture
def bluesky_account(db, workspace):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform="bluesky",
        account_platform_id="bsky-1",
        account_name="Orbita on Bluesky",
        connection_status="connected",
    )


@pytest.fixture
def issued_key(db, user, owner_memberships, workspace, instagram_account, bluesky_account):
    return services.issue_api_key(
        workspace=workspace,
        social_accounts=[instagram_account, bluesky_account],
        issued_by=user,
        name="reel-sound",
        permissions=list(PERMISSION_KEYS),
    )


@pytest.fixture
def client_with_token(issued_key):
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {issued_key.plaintext_token}")


def _post(client, body: dict):
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _track(**overrides):
    track = {
        "id": "587784541076604",
        "title": "Sommerregen",
        "artist": "Komiku",
        "duration_ms": 21000,
        "cover_url": "",
        "raw": {},
    }
    track.update(overrides)
    return track


@pytest.mark.django_db
class TestInstagramAudioLookup:
    def test_without_query_returns_trending(self, client_with_token, instagram_account):
        provider = MagicMock()
        provider.list_audio.return_value = [_track()]
        with patch("providers.get_provider", return_value=provider):
            r = client_with_token.get(f"/api/v1/accounts/{instagram_account.id}/instagram-audio")

        assert r.status_code == 200, r.content
        body = r.json()
        assert body["available"] is True
        assert body["trending"] is True
        assert body["tracks"] == [
            {
                "id": "587784541076604",
                "title": "Sommerregen",
                "artist": "Komiku",
                "duration_ms": 21000,
                "cover_url": "",
            }
        ]
        assert provider.list_audio.call_args.kwargs["search_query"] == ""

    def test_query_marks_the_result_as_a_search(self, client_with_token, instagram_account):
        provider = MagicMock()
        provider.list_audio.return_value = []
        with patch("providers.get_provider", return_value=provider):
            r = client_with_token.get(f"/api/v1/accounts/{instagram_account.id}/instagram-audio?q=walking")

        assert r.json()["trending"] is False
        assert provider.list_audio.call_args.kwargs["search_query"] == "walking"

    def test_lookup_failure_is_not_an_error_response(self, client_with_token, instagram_account):
        provider = MagicMock()
        provider.list_audio.side_effect = RuntimeError("Meta down")
        with patch("providers.get_provider", return_value=provider):
            r = client_with_token.get(f"/api/v1/accounts/{instagram_account.id}/instagram-audio")

        assert r.status_code == 200
        assert r.json() == {
            "available": False,
            "trending": False,
            "error": "Instagram did not return any audio",
            "tracks": [],
        }

    def test_non_instagram_account_is_422(self, client_with_token, bluesky_account):
        r = client_with_token.get(f"/api/v1/accounts/{bluesky_account.id}/instagram-audio")
        assert r.status_code == 422

    def test_account_outside_the_allowlist_is_403(self, client_with_token, workspace):
        from apps.social_accounts.models import SocialAccount

        stranger = SocialAccount.objects.create(
            workspace=workspace,
            platform="instagram",
            account_platform_id="ig-stranger",
            account_name="Not allowlisted",
            connection_status="connected",
        )
        r = client_with_token.get(f"/api/v1/accounts/{stranger.id}/instagram-audio")
        assert r.status_code == 403


@pytest.mark.django_db
class TestInstagramAudioOnCreate:
    def test_audio_lands_in_platform_extra(self, client_with_token, instagram_account):
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Reel caption",
                "platform_overrides": [
                    {
                        "social_account_id": str(instagram_account.id),
                        "instagram_audio": {
                            "audio_id": "587784541076604",
                            "audio_volume": 30,
                            "video_volume": 90,
                        },
                    }
                ],
                "action": "draft",
            },
        )
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get()
        assert pp.platform_extra == {
            "audio_id": "587784541076604",
            "audio_volume": 30,
            "video_volume": 90,
        }

    def test_volume_defaults_keep_the_voice_in_front(self, client_with_token, instagram_account):
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Reel caption",
                "platform_overrides": [
                    {
                        "social_account_id": str(instagram_account.id),
                        "instagram_audio": {"audio_id": "587784541076604"},
                    }
                ],
                "action": "draft",
            },
        )
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get()
        assert pp.platform_extra["audio_volume"] == 25
        assert pp.platform_extra["video_volume"] == 100

    def test_out_of_range_volume_is_rejected(self, client_with_token, instagram_account):
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Reel caption",
                "platform_overrides": [
                    {
                        "social_account_id": str(instagram_account.id),
                        "instagram_audio": {"audio_id": "1", "audio_volume": 150},
                    }
                ],
                "action": "draft",
            },
        )
        assert r.status_code == 422, r.content

    def test_audio_on_a_non_instagram_account_is_refused(self, client_with_token, bluesky_account):
        """Better a loud 422 than a setting that quietly does nothing."""
        r = _post(
            client_with_token,
            {
                "social_account_id": str(bluesky_account.id),
                "caption": "Post caption",
                "platform_overrides": [
                    {
                        "social_account_id": str(bluesky_account.id),
                        "instagram_audio": {"audio_id": "587784541076604"},
                    }
                ],
                "action": "draft",
            },
        )
        assert r.status_code == 422, r.content

    def test_post_without_audio_keeps_platform_extra_empty(self, client_with_token, instagram_account):
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Reel caption",
                "action": "draft",
            },
        )
        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {}
