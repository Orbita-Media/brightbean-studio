"""Agent-API-Seite der Instagram-Personenmarkierungen (``user_tags``).

``POST /api/v1/posts/`` und ``PATCH /api/v1/posts/{id}`` nehmen die
Markierungen über ``platform_overrides[].user_tags`` an
(``[{slide, username, x, y}]``, Folie ab 1). Sie landen in
``PlatformPost.platform_extra["user_tags"]`` und erreichen den Provider auf
demselben Weg wie jede andere kanalspezifische Einstellung
(``providers/instagram_user_tags.py``).

Abgewiesen wird schon hier, was beim Veröffentlichen Stunden später still
nichts täte: anderer Kanal, Folie gibt es nicht, Folie ist ein Video, Story.
"""

from __future__ import annotations

import json

import pytest
from django.core.files.base import ContentFile

# ruff: noqa: F401, F811  (imported for pytest fixture resolution, not called here)
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
        mime_type="video/mp4" if media_type == "video" else "image/jpeg",
        file_size=content.size,
        processing_status="completed",
    )
    asset.save()
    return asset


@pytest.fixture
def folien(workspace):
    return [_asset(workspace, f"team-0{n}.jpg", "image") for n in range(1, 6)]


@pytest.fixture
def video(workspace):
    return _asset(workspace, "reel.mp4", "video")


TEAM = [
    {"slide": 1, "username": "@stefanschott_", "x": 0.76, "y": 0.38},
    {"slide": 1, "username": "tobinstar", "x": 0.76, "y": 0.69},
    {"slide": 3, "username": "stefanschott_", "x": 0.62, "y": 0.26},
    {"slide": 5, "username": "tobinstar", "x": 0.63, "y": 0.3},
]


def _body(account_id, *, media=(), **override):
    return {
        "social_account_id": str(account_id),
        "caption": "Every fighter needs a corner.",
        "media_asset_ids": [str(m.id) for m in media],
        "platform_overrides": [{"social_account_id": str(account_id), **override}],
        "action": "draft",
    }


@pytest.mark.django_db
class TestUserTagsOnCreate:
    def test_markierungen_landen_normalisiert_in_platform_extra(self, client_with_token, instagram_account, folien):
        r = _post(client_with_token, _body(instagram_account.id, media=folien, user_tags=TEAM))

        assert r.status_code == 201, r.content
        assert PlatformPost.objects.get().platform_extra["user_tags"] == [
            {"slide": 1, "username": "stefanschott_", "x": 0.76, "y": 0.38},
            {"slide": 1, "username": "tobinstar", "x": 0.76, "y": 0.69},
            {"slide": 3, "username": "stefanschott_", "x": 0.62, "y": 0.26},
            {"slide": 5, "username": "tobinstar", "x": 0.63, "y": 0.3},
        ]

    def test_antwort_und_get_zeigen_die_markierungen(self, client_with_token, instagram_account, folien):
        r = _post(client_with_token, _body(instagram_account.id, media=folien, user_tags=TEAM))
        assert r.status_code == 201, r.content

        r = client_with_token.get(f"/api/v1/posts/{r.json()['id']}")
        assert r.status_code == 200, r.content
        tags = r.json()["platform_overrides"][0]["user_tags"]
        assert [(t["slide"], t["username"]) for t in tags] == [
            (1, "stefanschott_"),
            (1, "tobinstar"),
            (3, "stefanschott_"),
            (5, "tobinstar"),
        ]

    def test_beitrag_ohne_markierungen_bleibt_wie_bisher(self, client_with_token, instagram_account, folien):
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Ohne",
                "media_asset_ids": [str(m.id) for m in folien],
                "action": "draft",
            },
        )
        assert r.status_code == 201, r.content
        assert "user_tags" not in (PlatformPost.objects.get().platform_extra or {})
        r = client_with_token.get(f"/api/v1/posts/{r.json()['id']}")
        assert all(o.get("user_tags") is None for o in r.json()["platform_overrides"])

    def test_markierungen_neben_mitwirkenden(self, client_with_token, instagram_account, folien):
        r = _post(
            client_with_token,
            _body(instagram_account.id, media=folien, user_tags=TEAM, collaborators=["akajav", "orbitamedia_verlag"]),
        )
        assert r.status_code == 201, r.content
        extra = PlatformPost.objects.get().platform_extra
        assert extra["collaborators"] == ["akajav", "orbitamedia_verlag"]
        assert len(extra["user_tags"]) == 4

    def test_folie_die_es_nicht_gibt_ist_422(self, client_with_token, instagram_account, folien):
        r = _post(
            client_with_token,
            _body(
                instagram_account.id, media=folien[:2], user_tags=[{"slide": 3, "username": "a", "x": 0.5, "y": 0.5}]
            ),
        )
        assert r.status_code == 422, r.content
        assert "Folie 3" in r.content.decode()

    def test_video_folie_ist_422(self, client_with_token, instagram_account, folien, video):
        r = _post(
            client_with_token,
            _body(
                instagram_account.id,
                media=[folien[0], video],
                user_tags=[{"slide": 2, "username": "a", "x": 0.5, "y": 0.5}],
            ),
        )
        assert r.status_code == 422, r.content
        assert "Video" in r.content.decode()

    def test_koordinate_ausserhalb_ist_422(self, client_with_token, instagram_account, folien):
        r = _post(
            client_with_token,
            _body(instagram_account.id, media=folien, user_tags=[{"slide": 1, "username": "a", "x": 1.2, "y": 0.5}]),
        )
        assert r.status_code == 422, r.content

    def test_ungueltiger_name_ist_422(self, client_with_token, instagram_account, folien):
        r = _post(
            client_with_token,
            _body(
                instagram_account.id,
                media=folien,
                user_tags=[{"slide": 1, "username": "zwei worte", "x": 0.5, "y": 0.5}],
            ),
        )
        assert r.status_code == 422, r.content

    def test_anderer_kanal_ist_422(self, client_with_token, bluesky_account, folien):
        r = _post(
            client_with_token,
            _body(bluesky_account.id, media=folien, user_tags=[{"slide": 1, "username": "a", "x": 0.5, "y": 0.5}]),
        )
        assert r.status_code == 422, r.content
        assert "only valid for Instagram" in r.content.decode()

    def test_story_mit_markierung_ist_422(self, client_with_token, instagram_account, folien):
        r = _post(
            client_with_token,
            _body(
                instagram_account.id,
                media=folien[:1],
                post_type="story",
                user_tags=[{"slide": 1, "username": "a", "x": 0.5, "y": 0.5}],
            ),
        )
        assert r.status_code == 422, r.content
        assert "user_tags" in r.content.decode()


@pytest.mark.django_db
class TestUserTagsOnUpdate:
    def _entwurf(self, client, account_id, folien, **override):
        body = (
            _body(account_id, media=folien, **override)
            if override
            else {
                "social_account_id": str(account_id),
                "caption": "Erst ohne",
                "media_asset_ids": [str(m.id) for m in folien],
                "action": "draft",
            }
        )
        r = _post(client, body)
        assert r.status_code == 201, r.content
        return r.json()["id"]

    def _ov(self, account_id, **felder):
        return {"platform_overrides": [{"social_account_id": str(account_id), **felder}]}

    def test_nachtraeglich_setzen(self, client_with_token, instagram_account, folien):
        post_id = self._entwurf(client_with_token, instagram_account.id, folien)
        r = _patch(client_with_token, post_id, self._ov(instagram_account.id, user_tags=TEAM))
        assert r.status_code == 200, r.content
        assert len(PlatformPost.objects.get().platform_extra["user_tags"]) == 4

    def test_aufruf_ohne_das_feld_laesst_sie_stehen(self, client_with_token, instagram_account, folien):
        post_id = self._entwurf(client_with_token, instagram_account.id, folien, user_tags=TEAM)
        r = _patch(client_with_token, post_id, self._ov(instagram_account.id, collaborators=["akajav"]))
        assert r.status_code == 200, r.content
        extra = PlatformPost.objects.get().platform_extra
        assert len(extra["user_tags"]) == 4
        assert extra["collaborators"] == ["akajav"]

    def test_leere_liste_entfernt_sie(self, client_with_token, instagram_account, folien):
        post_id = self._entwurf(client_with_token, instagram_account.id, folien, user_tags=TEAM)
        r = _patch(client_with_token, post_id, self._ov(instagram_account.id, user_tags=[]))
        assert r.status_code == 200, r.content
        assert "user_tags" not in PlatformPost.objects.get().platform_extra

    def test_pruefung_gegen_die_medien_nach_dem_aufruf(self, client_with_token, instagram_account, folien):
        post_id = self._entwurf(client_with_token, instagram_account.id, folien[:2])
        r = _patch(
            client_with_token,
            post_id,
            {
                "media_asset_ids": [str(m.id) for m in folien],
                **self._ov(instagram_account.id, user_tags=[{"slide": 5, "username": "tobinstar", "x": 0.6, "y": 0.3}]),
            },
        )
        assert r.status_code == 200, r.content

        r = _patch(
            client_with_token,
            post_id,
            self._ov(instagram_account.id, user_tags=[{"slide": 6, "username": "tobinstar", "x": 0.6, "y": 0.3}]),
        )
        assert r.status_code == 422, r.content


def test_engine_reicht_markierungen_an_den_provider():
    """Vom gespeicherten ``platform_extra`` bis zum Provider: die Engine legt
    die Markierungen unverändert in ``content.extra``."""
    from unittest.mock import patch

    from apps.publisher.tests import _build_dispatch_mocks, _fake_attachment

    tags = [{"slide": 1, "username": "stefanschott_", "x": 0.76, "y": 0.38}]
    with (
        patch("apps.publisher.engine.get_provider") as get_provider,
        patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
    ):
        engine, platform_post, provider = _build_dispatch_mocks(
            platform="instagram",
            account_platform_id="ig-1",
            platform_extra={"user_tags": tags, "collaborators": ["akajav"]},
            attachments=[_fake_attachment("team-01.jpg"), _fake_attachment("team-02.jpg")],
        )
        get_provider.return_value = provider
        engine._dispatch_to_provider(platform_post)

    _token, content = provider.publish_post.call_args.args
    assert content.extra["user_tags"] == tags
    assert content.extra["collaborators"] == ["akajav"]
