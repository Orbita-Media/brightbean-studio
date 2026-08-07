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


def _patch(client, post_id, body: dict):
    return client.patch(
        f"/api/v1/posts/{post_id}", data=json.dumps(body), content_type="application/json"
    )


@pytest.mark.django_db
class TestCollaboratorsOnUpdate:
    """Mitwirkende nachtraeglich setzen.

    ``platform_overrides`` war bis zum 08.08.2026 create-only. Das zwang
    Aufrufer in einen Umweg: Acht fertige Entwuerfe, die ohne Mitwirkende
    angelegt worden waren, haetten neu angelegt und die alten von Hand
    geloescht werden muessen. Die Luecke zu schliessen ist billiger als der
    Umweg - und sie steht beim naechsten Mal nicht wieder da.
    """

    def _entwurf(self, client, account_id):
        r = _post(client, {"social_account_id": str(account_id), "caption": "Erst ohne", "action": "draft"})
        assert r.status_code == 201, r.content
        return r.json()["id"]

    def test_mitwirkende_lassen_sich_nachtraeglich_setzen(self, client_with_token, instagram_account):
        post_id = self._entwurf(client_with_token, instagram_account.id)
        assert PlatformPost.objects.get().platform_extra in ({}, None)

        r = _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(instagram_account.id), "collaborators": ["@kyocreepy"]}
                ]
            },
        )

        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get().platform_extra["collaborators"] == ["kyocreepy"]

    def test_die_antwort_zeigt_die_mitwirkenden(self, client_with_token, instagram_account):
        """Ohne das Feld in der Antwort kann keine Pruefung je einen Erfolg belegen.

        Genau daran ist der erste Pruefversuch gescheitert: Er las alle
        Entwuerfe zurueck, bekam ueberall "nicht gefunden" - und haette
        dasselbe auch bei perfektem Zustand gemeldet.
        """
        post_id = self._entwurf(client_with_token, instagram_account.id)
        _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(instagram_account.id), "collaborators": ["kyocreepy"]}
                ]
            },
        )

        r = client_with_token.get(f"/api/v1/posts/{post_id}")

        assert r.status_code == 200, r.content
        overrides = r.json()["platform_overrides"]
        assert len(overrides) == 1
        assert overrides[0]["collaborators"] == ["kyocreepy"]
        assert overrides[0]["platform"] == "instagram"

    def test_ein_aufruf_ohne_das_feld_laesst_sie_stehen(self, client_with_token, instagram_account):
        """Der wichtigste Test des Moduls.

        Ohne diese Unterscheidung wuerde jeder Aenderungsaufruf, der nur den
        Termin verschiebt, nebenbei die Mitwirkenden loeschen - derselbe
        Fehler, den der Composer bis zum 08.08.2026 hatte.
        """
        post_id = self._entwurf(client_with_token, instagram_account.id)
        _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(instagram_account.id), "collaborators": ["kyocreepy"]}
                ]
            },
        )

        r = _patch(client_with_token, post_id, {"internal_notes": "nur eine Notiz"})

        assert r.status_code == 200, r.content
        assert PlatformPost.objects.get().platform_extra["collaborators"] == ["kyocreepy"]

    def test_eine_leere_liste_entfernt_sie_bewusst(self, client_with_token, instagram_account):
        post_id = self._entwurf(client_with_token, instagram_account.id)
        _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(instagram_account.id), "collaborators": ["kyocreepy"]}
                ]
            },
        )

        r = _patch(
            client_with_token,
            post_id,
            {"platform_overrides": [{"social_account_id": str(instagram_account.id), "collaborators": []}]},
        )

        assert r.status_code == 200, r.content
        assert "collaborators" not in (PlatformPost.objects.get().platform_extra or {})

    def test_eine_kanalfassung_ueberlebt_das_setzen_der_mitwirkenden(
        self, client_with_token, instagram_account
    ):
        """Die Felder eines Overrides sind einzeln zu betrachten.

        Wer nur die Mitwirkenden setzt, darf die kanalspezifische Fassung
        nicht verlieren - sonst faellt der Beitrag auf den Basistext zurueck
        und reisst auf einem Kanal mit hartem Limit die Grenze.
        """
        r = _post(
            client_with_token,
            {
                "social_account_id": str(instagram_account.id),
                "caption": "Langer Basistext",
                "platform_overrides": [
                    {"social_account_id": str(instagram_account.id), "caption": "Kurze Fassung"}
                ],
                "action": "draft",
            },
        )
        post_id = r.json()["id"]

        _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(instagram_account.id), "collaborators": ["kyocreepy"]}
                ]
            },
        )

        pp = PlatformPost.objects.get()
        assert pp.platform_specific_caption == "Kurze Fassung"
        assert pp.platform_extra["collaborators"] == ["kyocreepy"]

    def test_ein_fremder_kanal_wird_abgewiesen(self, client_with_token, instagram_account, bluesky_account):
        post_id = self._entwurf(client_with_token, instagram_account.id)

        r = _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(bluesky_account.id), "collaborators": ["irgendwer"]}
                ]
            },
        )

        assert r.status_code == 422, r.content
        assert "must reference an account of this post" in r.content.decode()

    def test_mitwirkende_bleiben_instagram_vorbehalten(self, client_with_token, bluesky_account):
        r = _post(client_with_token, {"social_account_id": str(bluesky_account.id), "caption": "Text", "action": "draft"})
        post_id = r.json()["id"]

        r = _patch(
            client_with_token,
            post_id,
            {
                "platform_overrides": [
                    {"social_account_id": str(bluesky_account.id), "collaborators": ["irgendwer"]}
                ]
            },
        )

        assert r.status_code == 422, r.content
        assert "only valid for Instagram" in r.content.decode()
