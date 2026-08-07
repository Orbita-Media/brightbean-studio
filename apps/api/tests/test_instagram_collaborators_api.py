"""Agent API side of Instagram collaborators.

``POST /api/v1/posts`` takes the co-authors through
``platform_overrides[].collaborators``, which lands in
``PlatformPost.platform_extra`` and reaches the provider on the same path as
every other platform-specific setting.

Why this route has to exist at all: tagging someone does not put the post in
front of their followers, collaborating does. Instagram says so itself
(https://help.instagram.com/5861247717337470). The publishing tool composes its
posts over this API, so a collaboration has to be creatable without a human
opening the composer.

The fixtures come from the audio test module, which builds exactly the world
this needs: a workspace with one Instagram and one Bluesky account, and a key
whose allowlist covers both.
"""

from __future__ import annotations

import json

import pytest

from apps.composer.models import PlatformPost

# ruff: noqa: F401  (imported for pytest fixture resolution, not called here)
from apps.api.tests.test_instagram_audio_api import (
    bluesky_account,
    client_with_token,
    instagram_account,
    issued_key,
    organization,
    owner_memberships,
    user,
    workspace,
)


def _post(client, body: dict):
    # Trailing slash on purpose: without it Django answers 301 and the POST
    # body never reaches the view.
    return client.post("/api/v1/posts/", data=json.dumps(body), content_type="application/json")


def _body(account_id, collaborators, caption="Reel caption"):
    return {
        "social_account_id": str(account_id),
        "caption": caption,
        "platform_overrides": [
            {"social_account_id": str(account_id), "collaborators": collaborators}
        ],
        "action": "draft",
    }


@pytest.mark.django_db
class TestCollaboratorsOnCreate:
    def test_collaborators_land_in_platform_extra(self, client_with_token, instagram_account):
        r = _post(client_with_token, _body(instagram_account.id, ["sinascolorcats"]))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {"collaborators": ["sinascolorcats"]}

    def test_at_sign_and_whitespace_are_stripped_before_storing(
        self, client_with_token, instagram_account
    ):
        """The stored value is what a human later reads in the composer."""
        r = _post(client_with_token, _body(instagram_account.id, ["@fler", "  mr.pokee  "]))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra["collaborators"] == ["fler", "mr.pokee"]

    def test_the_same_name_twice_is_collapsed(self, client_with_token, instagram_account):
        r = _post(client_with_token, _body(instagram_account.id, ["Autor", "@autor", "zweiter"]))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra["collaborators"] == ["Autor", "zweiter"]

    def test_a_fourth_name_is_rejected_by_the_schema(self, client_with_token, instagram_account):
        """Graph documents three. Refusing here beats a silent drop later."""
        r = _post(client_with_token, _body(instagram_account.id, ["a", "b", "c", "d"]))

        assert r.status_code == 422, r.content

    def test_collaborators_survive_next_to_a_platform_sound(
        self, client_with_token, instagram_account
    ):
        """Both extras share one dict; neither may overwrite the other."""
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Reel caption",
                "platform_overrides": [
                    {
                        "social_account_id": str(instagram_account.id),
                        "instagram_audio": {"audio_id": "587784541076604"},
                        "collaborators": ["@akajav"],
                    }
                ],
                "action": "draft",
            },
        )

        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get().platform_extra
        assert extra["audio_id"] == "587784541076604"
        assert extra["collaborators"] == ["akajav"]

    def test_non_instagram_account_is_422(self, client_with_token, bluesky_account):
        """A setting that would quietly do nothing at publish time is refused."""
        r = _post(client_with_token, _body(bluesky_account.id, ["someone"]))

        assert r.status_code == 422, r.content
        assert "only valid for Instagram" in r.content.decode()

    def test_post_without_collaborators_keeps_platform_extra_empty(
        self, client_with_token, instagram_account
    ):
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

    def test_an_empty_list_stores_an_empty_list_not_a_name(
        self, client_with_token, instagram_account
    ):
        """Sending [] is a deliberate 'no co-authors', and must not crash."""
        r = _post(client_with_token, _body(instagram_account.id, []))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra == {"collaborators": []}
