"""Bildprüfung über die Agent-API (docs/BILDPRUEFUNG.md).

* ``GET /api/v1/media/bildpruefung/offen/``: Medien an künftigen Beiträgen
  ohne gültige Prüfung, nach Termin sortiert, ``bis``/``workspace``/``status``.
* ``POST /api/v1/media/{id}/bildpruefung/``: speichert das Prüfobjekt;
  409 bei falschem sha256, 422 bei verletztem Vertrag, 403 ohne Recht.
* ``GET /api/v1/media/{id}``: liefert ``bildpruefung`` mit.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from datetime import timedelta

import pytest
from django.core.files.base import ContentFile
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership

BILD = b"\x89PNG\r\n\x1a\nsimply-bowls"
SHA = hashlib.sha256(BILD).hexdigest()


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture(autouse=True)
def _media_root(settings):
    pfad = tempfile.mkdtemp()
    settings.MEDIA_ROOT = pfad
    yield
    shutil.rmtree(pfad, ignore_errors=True)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="bildwache@example.com", password="testpass123", name="Bildwache", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Orbita")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Verlag", organization=organization)


@pytest.fixture
def social_account(db, workspace):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-1",
        account_name="Orbita Media Verlag",
        connection_status="connected",
    )


def _key(user, organization, workspace, social_account, *, org_role="owner", ws_role="owner", perms=None):
    OrgMembership.objects.create(user=user, organization=organization, org_role=org_role)
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=ws_role)
    return services.issue_api_key(
        workspace=workspace,
        social_accounts=[social_account],
        issued_by=user,
        name="bildwache",
        permissions=perms if perms is not None else list(PERMISSION_KEYS),
    )


@pytest.fixture
def client(db, user, organization, workspace, social_account):
    key = _key(user, organization, workspace, social_account)
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


def _asset(workspace, name="bowl.png", inhalt=BILD, pruefung=None):
    from apps.media_library.models import MediaAsset

    asset = MediaAsset(
        organization=workspace.organization,
        workspace=workspace,
        filename=name,
        media_type="image",
        mime_type="image/png",
        bildpruefung=pruefung,
    )
    asset.file.save(name, ContentFile(inhalt), save=False)
    asset.save()
    return asset


def _beitrag(workspace, social_account, asset, *, termin, status="scheduled", caption="Simply Bowls"):
    from apps.composer.models import PlatformPost, Post, PostMedia

    post = Post.objects.create(workspace=workspace, caption=caption)
    PostMedia.objects.create(post=post, media_asset=asset)
    PlatformPost.objects.create(post=post, social_account=social_account, status=status, scheduled_at=termin)
    return post


def _pruefung(status="bestanden", sha=SHA, **extra):
    daten = {
        "status": status,
        "sha256": sha,
        "pruefer": "bildwache/dirigent",
        "modelle": ["claude-opus-5-5", "gemini-3-pro"],
        "befunde": [],
        "geprueft_am": "2026-10-29T22:15:00Z",
        "notiz": "",
    }
    daten.update(extra)
    return daten


@pytest.mark.django_db
class TestOffen:
    def test_liste_nach_termin_mit_vertragsfeldern(self, client, workspace, social_account):
        später = _asset(workspace, name="b.png", inhalt=b"b")
        früher = _asset(workspace, name="a.png")
        _beitrag(workspace, social_account, später, termin=timezone.now() + timedelta(days=2))
        _beitrag(workspace, social_account, früher, termin=timezone.now() + timedelta(hours=2), caption="Cosmos " * 50)
        geprüft = _asset(workspace, name="ok.png", inhalt=b"ok", pruefung=None)
        from apps.media_library.bildpruefung import pruefung_speichern

        pruefung_speichern(geprüft, _pruefung(sha=hashlib.sha256(b"ok").hexdigest()))
        _beitrag(workspace, social_account, geprüft, termin=timezone.now() + timedelta(hours=1))

        r = client.get("/api/v1/media/bildpruefung/offen/")
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["anzahl"] == 2
        assert body["pflicht_an"] is False
        assert body["workspace_id"] == str(workspace.id)
        assert [i["filename"] for i in body["items"]] == ["a.png", "b.png"]
        erstes = body["items"][0]
        for feld in ("id", "media_type", "url", "sha256", "termin", "plattformen", "workspace_id", "beitragstext"):
            assert feld in erstes
        assert erstes["plattformen"] == ["instagram"]
        assert erstes["grund"] == "fehlt"
        assert erstes["termin"].endswith("Z")
        assert len(erstes["beitragstext"]) <= 160
        assert erstes["url"]

    def test_bis_filtert_und_entwürfe_ohne_termin_fallen_raus(self, client, workspace, social_account):
        _beitrag(
            workspace, social_account, _asset(workspace, name="bald.png"), termin=timezone.now() + timedelta(hours=3)
        )
        _beitrag(
            workspace,
            social_account,
            _asset(workspace, name="fern.png", inhalt=b"f"),
            termin=timezone.now() + timedelta(days=20),
        )
        _beitrag(
            workspace, social_account, _asset(workspace, name="entwurf.png", inhalt=b"e"), termin=None, status="draft"
        )

        alle = client.get("/api/v1/media/bildpruefung/offen/").json()
        assert [i["filename"] for i in alle["items"]] == ["bald.png", "fern.png", "entwurf.png"]

        bis = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = client.get(f"/api/v1/media/bildpruefung/offen/?bis={bis}")
        assert [i["filename"] for i in r.json()["items"]] == ["bald.png"]

    def test_fremder_workspace_403(self, client, workspace, organization):
        from apps.workspaces.models import Workspace

        anderer = Workspace.objects.create(name="Anderer", organization=organization)
        r = client.get(f"/api/v1/media/bildpruefung/offen/?workspace={anderer.id}")
        assert r.status_code == 403
        assert client.get(f"/api/v1/media/bildpruefung/offen/?workspace={workspace.id}").status_code == 200

    def test_status_durchgefallen(self, client, workspace, social_account):
        asset = _asset(workspace, pruefung=_pruefung("durchgefallen", befunde=["Buch auf der Schüssel"]))
        _beitrag(workspace, social_account, asset, termin=timezone.now() + timedelta(hours=5))

        offen = client.get("/api/v1/media/bildpruefung/offen/").json()
        assert offen["anzahl"] == 0
        assert offen["durchgefallen_anzahl"] == 1
        r = client.get("/api/v1/media/bildpruefung/offen/?status=durchgefallen").json()
        assert r["items"][0]["bildpruefung"]["befunde"] == ["Buch auf der Schüssel"]

    def test_ohne_key_401(self, db):
        assert _SecureClient().get("/api/v1/media/bildpruefung/offen/").status_code == 401


@pytest.mark.django_db
class TestPost:
    def test_speichert_und_detail_liefert_mit(self, client, workspace):
        asset = _asset(workspace)
        r = client.post(f"/api/v1/media/{asset.id}/bildpruefung/", data=_pruefung(), content_type="application/json")
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["bildpruefung"]["status"] == "bestanden"
        assert body["bildpruefung_status"] == "bestanden"
        assert body["bildpruefung_gueltig"] is True
        assert body["sha256"] == SHA

        detail = client.get(f"/api/v1/media/{asset.id}").json()
        assert detail["bildpruefung"]["pruefer"] == "bildwache/dirigent"
        assert detail["bildpruefung"]["modelle"] == ["claude-opus-5-5", "gemini-3-pro"]
        assert detail["bildpruefung_status"] == "bestanden"

    def test_falscher_sha_409(self, client, workspace):
        asset = _asset(workspace)
        r = client.post(
            f"/api/v1/media/{asset.id}/bildpruefung/",
            data=_pruefung(sha="e" * 64),
            content_type="application/json",
        )
        assert r.status_code == 409
        body = r.json()
        assert body["error"] == "conflict"
        assert body["sha256_aktuell"] == SHA
        asset.refresh_from_db()
        assert asset.bildpruefung is None

    def test_durchgefallen_ohne_befund_422(self, client, workspace):
        asset = _asset(workspace)
        r = client.post(
            f"/api/v1/media/{asset.id}/bildpruefung/",
            data=_pruefung("durchgefallen"),
            content_type="application/json",
        )
        assert r.status_code == 422
        assert "Befund" in r.json()["detail"]

    def test_unbekannter_status_422(self, client, workspace):
        asset = _asset(workspace)
        r = client.post(
            f"/api/v1/media/{asset.id}/bildpruefung/", data=_pruefung("passt schon"), content_type="application/json"
        )
        assert r.status_code == 422

    def test_ohne_edit_media_403(self, db, user, organization, workspace, social_account):
        key = _key(
            user, organization, workspace, social_account, perms=[p for p in PERMISSION_KEYS if p != "edit_media"]
        )
        c = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")
        asset = _asset(workspace)
        r = c.post(f"/api/v1/media/{asset.id}/bildpruefung/", data=_pruefung(), content_type="application/json")
        assert r.status_code == 403

    def test_von_hand_nur_für_owner_admin(self, db, user, organization, workspace, social_account):
        # Keys stellt nur ein Owner/Admin aus; danach zurückgestuft, gilt die Freigabe nicht mehr.
        key = _key(user, organization, workspace, social_account)
        OrgMembership.objects.filter(user=user).update(org_role="member")
        WorkspaceMembership.objects.filter(user=user).update(workspace_role="editor")
        c = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")
        asset = _asset(workspace)
        daten = _pruefung("von_hand_freigegeben", notiz="Noah hat es angesehen")
        r = c.post(f"/api/v1/media/{asset.id}/bildpruefung/", data=daten, content_type="application/json")
        assert r.status_code == 403

    def test_von_hand_braucht_notiz(self, client, workspace):
        asset = _asset(workspace)
        r = client.post(
            f"/api/v1/media/{asset.id}/bildpruefung/",
            data=_pruefung("von_hand_freigegeben"),
            content_type="application/json",
        )
        assert r.status_code == 422

    def test_nach_prüfung_nicht_mehr_offen(self, client, workspace, social_account):
        asset = _asset(workspace)
        _beitrag(workspace, social_account, asset, termin=timezone.now() + timedelta(hours=2))
        assert client.get("/api/v1/media/bildpruefung/offen/").json()["anzahl"] == 1
        client.post(f"/api/v1/media/{asset.id}/bildpruefung/", data=_pruefung(), content_type="application/json")
        assert client.get("/api/v1/media/bildpruefung/offen/").json()["anzahl"] == 0
