"""HTTP-Tests für die Alt-Text-Pflege in der Mediathek.

Bis dahin liess sich ein Alternativtext nur beim Hochladen über die Agent-API
setzen. Wer einen Tippfehler korrigieren wollte, kam an das Feld nicht heran –
weder die Mediathek noch der Composer zeigten es. Der Text liegt am Medium,
eine Korrektur hier wirkt deshalb für jeden Beitrag, der das Bild benutzt.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.media_library.models import MAX_ALT_TEXT_LENGTH, MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


class AltTextEndpointTests(TestCase):
    """POST /workspace/<id>/media/<asset_id>/alt-text/"""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(
            user=self.user,
            organization=self.org,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            uploaded_by=self.user,
            file="media_library/tests/asset.png",
            filename="asset.png",
            media_type=MediaAsset.MediaType.IMAGE,
            mime_type="image/png",
            file_size=128,
            source="upload",
            alt_text="Folie 1: Der alte Text",
        )
        self.client.force_login(self.user)
        self.url = reverse(
            "media_library:asset_alt_text",
            kwargs={"workspace_id": self.workspace.id, "asset_id": self.asset.id},
        )

    def _post(self, text, htmx=False):
        kwargs = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(self.url, data={"alt_text": text}, **kwargs)

    def test_speichert_den_neuen_text(self):
        response = self._post("Folie 1: Größe zählt – so geht das")

        self.assertEqual(response.status_code, 200)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.alt_text, "Folie 1: Größe zählt – so geht das")

    def test_der_tippfehler_laesst_sich_korrigieren(self):
        self._post("Folie 1: Der neue Text")
        self.asset.refresh_from_db()

        self.assertEqual(self.asset.alt_text, "Folie 1: Der neue Text")

    def test_leert_das_feld_wenn_leer_gesendet(self):
        self._post("")

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.alt_text, "")

    def test_schneidet_ueberlangen_text_auf_das_kanal_limit(self):
        self._post("x" * (MAX_ALT_TEXT_LENGTH + 500))

        self.asset.refresh_from_db()
        self.assertEqual(len(self.asset.alt_text), MAX_ALT_TEXT_LENGTH)

    def test_entfernt_fuehrende_und_folgende_leerzeichen(self):
        self._post("   Folie 1   ")

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.alt_text, "Folie 1")

    def test_htmx_liefert_das_feld_mit_bestaetigung_zurueck(self):
        response = self._post("Folie 1: neu", htmx=True)
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Folie 1: neu", html)
        self.assertIn("Saved", html)

    def test_json_antwort_ohne_htmx(self):
        response = self._post("Folie 1: neu")

        self.assertEqual(response.json(), {"alt_text": "Folie 1: neu"})

    def test_fremdzeichen_werden_am_feld_gemeldet(self):
        # Kyrillisches и/н/г – der Publisher wuerde den Beitrag ablehnen, also
        # steht die Warnung direkt am Eingabefeld.
        response = self._post("Folie 1: du übersprингst das", htmx=True)
        html = response.content.decode("utf-8")

        self.assertIn("will not publish", html)
        self.assertIn("U+0438", html)

    def test_sauberer_text_erzeugt_keine_warnung(self):
        response = self._post("Folie 1: du überspringst das 🚀", htmx=True)

        self.assertNotIn("will not publish", response.content.decode("utf-8"))

    def test_get_ist_nicht_erlaubt(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_ohne_anmeldung_umleitung(self):
        self.client.logout()
        response = self._post("egal")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_fremder_arbeitsbereich_bekommt_403(self):
        other = User.objects.create_user(
            email="outsider@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        other_org = Organization.objects.create(name="Other Org")
        OrgMembership.objects.create(user=other, organization=other_org, org_role=OrgMembership.OrgRole.OWNER)
        self.client.force_login(other)

        response = self._post("fremd")

        self.assertEqual(response.status_code, 403)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.alt_text, "Folie 1: Der alte Text")

    def test_html_wird_beim_rendern_entschaerft(self):
        response = self._post("<script>alert(1)</script>", htmx=True)
        html = response.content.decode("utf-8")

        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)


class AltTextImDetailbereichTests(TestCase):
    """Das Feld muss im Detailbereich der Mediathek auch wirklich auftauchen."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner2@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Org 2")
        self.workspace = Workspace.objects.create(organization=self.org, name="WS 2")
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            uploaded_by=self.user,
            file="media_library/tests/asset.png",
            filename="asset.png",
            media_type=MediaAsset.MediaType.IMAGE,
            mime_type="image/png",
            file_size=128,
            source="upload",
            alt_text="Folie 3: Was auf dem Bild steht",
        )
        self.client.force_login(self.user)

    def test_detailbereich_zeigt_das_eingabefeld_mit_dem_text(self):
        url = reverse(
            "media_library:asset_detail",
            kwargs={"workspace_id": self.workspace.id, "asset_id": self.asset.id},
        )

        html = self.client.get(url, HTTP_HX_REQUEST="true").content.decode("utf-8")

        self.assertIn("Alt text", html)
        self.assertIn("Folie 3: Was auf dem Bild steht", html)
        self.assertIn(f"alt-text-editor-{self.asset.id}", html)

    def test_ein_dokument_bekommt_kein_alt_text_feld(self):
        self.asset.media_type = MediaAsset.MediaType.DOCUMENT
        self.asset.save(update_fields=["media_type"])
        url = reverse(
            "media_library:asset_detail",
            kwargs={"workspace_id": self.workspace.id, "asset_id": self.asset.id},
        )

        html = self.client.get(url, HTTP_HX_REQUEST="true").content.decode("utf-8")

        self.assertNotIn(f"alt-text-editor-{self.asset.id}", html)
