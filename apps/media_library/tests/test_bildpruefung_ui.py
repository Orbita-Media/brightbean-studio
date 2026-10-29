"""Bildprüfung in der Oberfläche (docs/BILDPRUEFUNG.md).

* Abzeichen „Bildprüfung: bestanden / durchgefallen / offen“ in Mediathek
  (Raster, Liste, Detail) und Composer.
* „Von Hand freigeben“ nur für Owner/Admin, Begründung Pflicht; schreibt
  ``von_hand_freigegeben`` mit dem Benutzer als Prüfer und dem sha256 der Datei.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile

import pytest
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership

BILD = b"\x89PNG\r\n\x1a\ncosmos-szene"
SHA = hashlib.sha256(BILD).hexdigest()


@pytest.fixture(autouse=True)
def _media_root(settings):
    pfad = tempfile.mkdtemp()
    settings.MEDIA_ROOT = pfad
    yield
    shutil.rmtree(pfad, ignore_errors=True)


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Orbita")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Verlag", organization=organization)


def _user(email, organization, workspace, *, org_role="owner", ws_role="owner"):
    from apps.accounts.models import User

    user = User.objects.create_user(email=email, password="testpass123", name="Noah", tos_accepted_at=timezone.now())
    OrgMembership.objects.create(user=user, organization=organization, org_role=org_role)
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=ws_role)
    return user


@pytest.fixture
def owner(db, organization, workspace):
    return _user("owner@example.com", organization, workspace)


@pytest.fixture
def editor(db, organization, workspace):
    return _user("editor@example.com", organization, workspace, org_role="member", ws_role="editor")


def _asset(workspace, *, name="cosmos.png", pruefung=None, media_type="image", geteilt=False):
    asset = MediaAsset(
        organization=workspace.organization,
        workspace=None if geteilt else workspace,
        filename=name,
        media_type=media_type,
        mime_type="image/png",
        bildpruefung=pruefung,
    )
    asset.file.save(name, ContentFile(BILD), save=False)
    asset.save()
    return asset


def _pruefung(status, **extra):
    daten = {
        "status": status,
        "sha256": SHA,
        "pruefer": "Bildwache (Social-Agent)",
        "modelle": ["claude-opus-5-5"],
        "befunde": [],
        "geprueft_am": "2026-10-29T22:15:00Z",
        "notiz": "",
    }
    daten.update(extra)
    return daten


def _url(workspace, asset):
    return reverse(
        "media_library:asset_bildpruefung_freigeben",
        kwargs={"workspace_id": workspace.id, "asset_id": asset.id},
    )


def _detail(client, workspace, asset):
    return client.get(
        reverse("media_library:asset_detail", kwargs={"workspace_id": workspace.id, "asset_id": asset.id}),
        HTTP_HX_REQUEST="true",
    )


@pytest.mark.django_db
class TestAbzeichen:
    def test_mediathek_zeigt_offen_bestanden_und_durchgefallen(self, client, owner, workspace):
        offen = _asset(workspace, name="offen.png")
        bestanden = _asset(workspace, name="gut.png", pruefung=_pruefung("bestanden"))
        durch = _asset(
            workspace,
            name="schlecht.png",
            pruefung=_pruefung("durchgefallen", befunde=["Buch steht auf dem Schüsselrand"]),
        )
        client.force_login(owner)

        html = client.get(reverse("media_library:index", kwargs={"workspace_id": workspace.id})).content.decode()

        assert f'data-bp-abzeichen="{offen.id}"' in html
        assert f'data-bp-abzeichen="{bestanden.id}"' in html
        assert f'data-bp-abzeichen="{durch.id}"' in html
        assert 'data-bp-status="offen"' in html
        assert 'data-bp-status="bestanden"' in html
        assert 'data-bp-status="durchgefallen"' in html
        assert "Bildprüfung: durchgefallen – Buch steht auf dem Schüsselrand" in html
        # Der Dialog ist einmal auf der Seite.
        assert html.count("data-bp-dialog") == 1

    def test_veraltete_pruefung_gilt_als_offen(self, client, owner, workspace):
        asset = _asset(workspace, pruefung=_pruefung("bestanden", sha256="a" * 64))
        client.force_login(owner)

        html = _detail(client, workspace, asset).content.decode()

        assert 'data-bp-status="offen"' in html
        assert "älteren Fassung der Datei" in html

    def test_dokumente_bekommen_kein_abzeichen(self, client, owner, workspace):
        asset = _asset(workspace, name="info.pdf", media_type="document")
        client.force_login(owner)

        html = _detail(client, workspace, asset).content.decode()

        assert "data-bp-abzeichen" not in html
        assert "data-bp-block" not in html

    def test_detail_zeigt_befunde_und_freigabeknopf_fuer_owner(self, client, owner, workspace):
        asset = _asset(workspace, pruefung=_pruefung("durchgefallen", befunde=["Lichtstrahl durchs Cover"]))
        client.force_login(owner)

        html = _detail(client, workspace, asset).content.decode()

        assert f'data-bp-block="{asset.id}"' in html
        assert "Lichtstrahl durchs Cover" in html
        assert "Von Hand freigeben" in html
        assert "Dieses Medium wird nicht veröffentlicht" in html

    def test_editor_sieht_abzeichen_aber_keinen_freigabeknopf(self, client, editor, workspace):
        asset = _asset(workspace)
        client.force_login(editor)

        html = _detail(client, workspace, asset).content.decode()

        assert f'data-bp-abzeichen="{asset.id}"' in html
        block = html[html.index("data-bp-block") :]
        assert "Von Hand freigeben" not in block.split("<!-- Alt text")[0]
        daten = _abzeichen_daten(html, asset)
        assert daten["darf"] is False
        assert daten["url"] == ""

    def test_composer_vorschaubild_zeigt_abzeichen(self, owner, workspace):
        from apps.composer.models import Post, PostMedia

        asset = _asset(workspace, pruefung=_pruefung("durchgefallen", befunde=["Matsch in der Bowl"]))
        post = Post.objects.create(workspace=workspace, caption="Simply Bowls")
        anhang = PostMedia.objects.create(post=post, media_asset=asset)
        request = RequestFactory().get("/")
        request.user = owner

        html = render_to_string(
            "composer/partials/media_list.html",
            {"media_attachments": [anhang], "post": post, "workspace": workspace},
            request=request,
        )

        assert 'data-bp-form="thumb"' in html
        assert ">durchgefallen<" in html
        assert "Matsch in der Bowl" in html


def _abzeichen_daten(html, asset):
    import html as html_mod
    import re

    treffer = re.search(r'data-bp-abzeichen="' + str(asset.id) + r'"[^>]*?data-bp="([^"]*)"', html, re.S)
    assert treffer, "Abzeichen mit Daten fehlt"
    return json.loads(html_mod.unescape(treffer.group(1)))


@pytest.mark.django_db
class TestVonHandFreigeben:
    def test_owner_gibt_mit_begruendung_frei(self, client, owner, workspace):
        asset = _asset(workspace, pruefung=_pruefung("durchgefallen", befunde=["Grenzfall Schatten"]))
        client.force_login(owner)

        antwort = client.post(_url(workspace, asset), {"notiz": "Schatten passt, Autorin hat zugestimmt."})

        assert antwort.status_code == 200
        asset.refresh_from_db()
        assert asset.bildpruefung["status"] == "von_hand_freigegeben"
        assert asset.bildpruefung["sha256"] == SHA
        assert asset.bildpruefung["pruefer"] == "Noah <owner@example.com>"
        assert asset.bildpruefung["notiz"] == "Schatten passt, Autorin hat zugestimmt."
        # Die überstimmten Befunde bleiben sichtbar.
        assert asset.bildpruefung["befunde"] == ["Grenzfall Schatten"]
        daten = antwort.json()
        assert daten["bildpruefung"]["status"] == "von_hand_freigegeben"
        assert daten["bildpruefung"]["freigegeben"] is True
        assert set(daten["abzeichen"]) == {"thumb", "karte", "zeile"}
        for html in daten["abzeichen"].values():
            assert 'data-bp-status="von_hand_freigegeben"' in html
        assert f'data-bp-block="{asset.id}"' in daten["block"]
        assert "Von Hand freigeben" not in daten["block"]

    def test_ohne_begruendung_422(self, client, owner, workspace):
        asset = _asset(workspace)
        client.force_login(owner)

        antwort = client.post(_url(workspace, asset), {"notiz": "   "})

        assert antwort.status_code == 422
        assert "begründe" in antwort.json()["fehler"]
        asset.refresh_from_db()
        assert asset.bildpruefung is None

    def test_editor_darf_nicht_403(self, client, editor, workspace):
        asset = _asset(workspace)
        client.force_login(editor)

        antwort = client.post(_url(workspace, asset), {"notiz": "passt schon"})

        assert antwort.status_code == 403
        asset.refresh_from_db()
        assert asset.bildpruefung is None

    def test_org_admin_ohne_workspace_owner_darf(self, client, organization, workspace):
        admin = _user("admin@example.com", organization, workspace, org_role="admin", ws_role="editor")
        asset = _asset(workspace)
        client.force_login(admin)

        assert client.post(_url(workspace, asset), {"notiz": "geprüft am Bildschirm"}).status_code == 200

    def test_geteiltes_medium_nur_org_owner_oder_admin(self, client, organization, workspace):
        ws_owner = _user("wsowner@example.com", organization, workspace, org_role="member", ws_role="owner")
        asset = _asset(workspace, geteilt=True)
        client.force_login(ws_owner)

        assert client.post(_url(workspace, asset), {"notiz": "passt"}).status_code == 403
        asset.refresh_from_db()
        assert asset.bildpruefung is None

    def test_nur_post(self, client, owner, workspace):
        asset = _asset(workspace)
        client.force_login(owner)

        assert client.get(_url(workspace, asset)).status_code == 405

    def test_fremder_workspace_404(self, client, owner, organization, workspace):
        from apps.workspaces.models import Workspace

        anderer = Workspace.objects.create(name="Fremd", organization=organization)
        asset = _asset(anderer)
        client.force_login(owner)

        assert client.post(_url(workspace, asset), {"notiz": "passt"}).status_code == 404
