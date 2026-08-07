"""Die kanalspezifische Fassung eines Beitrags darf beim Speichern nicht verschwinden.

Hintergrund: Ein Beitrag trägt für Bluesky eine eigene Kurzfassung
(``platform_specific_caption``), weil Bluesky bei 300 Zeichen dichtmacht,
während der Basistext für Instagram gedacht ist. Ein Klick auf „Save Draft"
im Composer hat diese Kurzfassung entfernt – ohne Meldung. Danach wäre auf
Bluesky der volle Text rausgegangen und dort aufgelaufen.

Zwei Hälften, beide hier abgedeckt:

* Speichern – ein Feld, das das Formular gar nicht mitschickt, heisst nicht
  „Override entfernen".
* Vorschau – sie muss den gespeicherten Stand zeigen, sonst meldet sie eine
  Überschreitung für einen Text, den niemand veröffentlichen würde, und
  verleitet dazu, den Basistext zu kürzen.
"""

from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

# Echte Grössenordnung aus dem Verteiler: Basistext für Instagram, Kurzfassung
# für Bluesky. Der Basistext sprengt das Bluesky-Limit von 300 Zeichen, die
# Kurzfassung nicht – genau daran hängt der Schaden.
# Ohne Leerzeichen am Ende: Das Formular schneidet Rand-Leerzeichen ab, ein
# Vergleich mit einem angehängten Leerzeichen würde die Prüfung selbst zum
# Fehlalarm machen.
BASISTEXT = ("Instagram-Fassung. " * 28).strip()  # 531 Zeichen
KURZFASSUNG = ("Bluesky-Kurzfassung, passt in 300 Zeichen. " * 6).strip()  # 251 Zeichen


class PlattformFassungBasis(TestCase):
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
        self.client.force_login(self.user)

        self.bluesky = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="bluesky",
            account_platform_id="did:plc:orbita",
            account_name="Orbita Media",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Karussell",
            caption=BASISTEXT,
        )
        self.pp = PlatformPost.objects.create(
            post=self.post,
            social_account=self.bluesky,
            status=PlatformPost.Status.DRAFT,
            platform_specific_caption=KURZFASSUNG,
            platform_specific_title="Kurzer Titel",
            platform_specific_first_comment="Erster Kommentar nur auf diesem Kanal",
        )
        self.save_url = reverse(
            "composer:save_post_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
        )

    def _payload(self, **felder):
        payload = {
            "action": "save_draft",
            "title": "Karussell",
            "caption": BASISTEXT,
            "tags": "",
            "selected_accounts": str(self.bluesky.id),
        }
        payload.update(felder)
        return payload


class SpeichernBehaeltDieFassung(PlattformFassungBasis):
    """Ein Speichervorgang darf eine gespeicherte Fassung nicht entfernen."""

    def test_fehlendes_feld_laesst_die_kurzfassung_stehen(self):
        # Der Fall, der den Schaden gemacht hat: Das Formular schickt das Feld
        # gar nicht mit (Speichern aus einem anderen Bereich der Oberfläche).
        antwort = self.client.post(self.save_url, data=self._payload())

        self.assertIn(antwort.status_code, (200, 204, 302))
        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_specific_caption, KURZFASSUNG)
        self.assertEqual(self.pp.effective_caption, KURZFASSUNG)

    def test_fehlendes_feld_laesst_titel_und_ersten_kommentar_stehen(self):
        # Ein Feld für den ersten Kommentar rendert der Composer überhaupt
        # nicht – ohne diesen Schutz wird es bei jedem Speichern geleert.
        self.client.post(self.save_url, data=self._payload())

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_specific_title, "Kurzer Titel")
        self.assertEqual(
            self.pp.platform_specific_first_comment,
            "Erster Kommentar nur auf diesem Kanal",
        )

    def test_leeres_feld_entfernt_die_fassung_bewusst(self):
        # „Remove override": Das Feld ist dabei und leer – das ist eine
        # Entscheidung und muss weiterhin greifen.
        antwort = self.client.post(
            self.save_url,
            data=self._payload(**{f"override_caption_{self.bluesky.id}": ""}),
        )

        self.assertIn(antwort.status_code, (200, 204, 302))
        self.pp.refresh_from_db()
        self.assertIsNone(self.pp.platform_specific_caption)
        self.assertEqual(self.pp.effective_caption, BASISTEXT)

    def test_nur_leerzeichen_zaehlt_als_entfernt(self):
        self.client.post(
            self.save_url,
            data=self._payload(**{f"override_caption_{self.bluesky.id}": "   \n  "}),
        )

        self.pp.refresh_from_db()
        self.assertIsNone(self.pp.platform_specific_caption)

    def test_geaenderte_fassung_wird_uebernommen(self):
        neu = "Neue Kurzfassung für Bluesky."
        self.client.post(
            self.save_url,
            data=self._payload(**{f"override_caption_{self.bluesky.id}": neu}),
        )

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_specific_caption, neu)
        self.assertEqual(self.pp.effective_caption, neu)

    def test_eine_runde_durch_das_formular_aendert_nichts(self):
        """Laden und unverändert speichern muss folgenlos bleiben."""
        seite = self.client.get(
            reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id})
        )
        self.assertEqual(seite.status_code, 200)

        # Das Formular trägt die gespeicherten Fassungen – also gehen sie beim
        # Absenden auch wieder mit raus.
        self.client.post(
            self.save_url,
            data=self._payload(
                **{
                    f"override_caption_{self.bluesky.id}": KURZFASSUNG,
                    f"override_title_{self.bluesky.id}": "Kurzer Titel",
                }
            ),
        )

        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_specific_caption, KURZFASSUNG)
        self.assertEqual(self.pp.platform_specific_title, "Kurzer Titel")


class ComposerLaedtDieFassung(PlattformFassungBasis):
    """Der Composer muss zeigen, was wirklich rausgeht."""

    def test_gespeicherte_fassung_steht_im_formular(self):
        antwort = self.client.get(
            reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id})
        )

        self.assertEqual(antwort.status_code, 200)
        inhalt = antwort.content.decode()
        self.assertIn("composer-override-captions", inhalt)
        self.assertIn(KURZFASSUNG.strip()[:60], inhalt)
        self.assertEqual(antwort.context["override_captions"][str(self.bluesky.id)], KURZFASSUNG)
        self.assertEqual(antwort.context["override_titles"][str(self.bluesky.id)], "Kurzer Titel")

    def test_neuer_beitrag_startet_ohne_fassungen(self):
        antwort = self.client.get(reverse("composer:compose", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["override_captions"], {})
        self.assertEqual(antwort.context["override_titles"], {})

    def test_die_felder_haengen_am_zustand_und_nicht_in_der_luft(self):
        """Ohne Bindung rendert das Feld leer und räumt beim Speichern ab."""
        vorlage = Path("templates/composer/compose.html").read_text(encoding="utf-8")

        self.assertIn('x-model="overrideCaptions[accId]"', vorlage)
        self.assertIn('x-model="overrideTitles[accId]"', vorlage)
        # Das Zuklappen allein würde den Text im verborgenen Feld stehen
        # lassen – er ginge beim nächsten Speichern wieder mit.
        self.assertIn('@click="removeOverride(accId)"', vorlage)

    def test_der_zaehler_am_basistext_ueberspringt_kanaele_mit_eigener_fassung(self):
        """Sonst steht dort rot eine Grenze, die für diesen Text nicht gilt.

        Genau diese Meldung verleitet dazu, den Basistext zu kürzen – und der
        geht auf Instagram und Facebook raus.
        """
        vorlage = Path("templates/composer/compose.html").read_text(encoding="utf-8")

        self.assertIn("sharedCaptionLimit()", vorlage)
        self.assertIn("filter(id => !this.overrideCaptions[id])", vorlage)
        # Die alte Rechnung nahm das kleinste Limit aller gewählten Kanäle,
        # ohne zu fragen, ob der Kanal den Basistext überhaupt verwendet.
        self.assertNotIn("charLimits[id]?.limit || 9999", vorlage)


class VorschauZeigtDenGespeichertenStand(PlattformFassungBasis):
    """Die Vorschau darf keine Überschreitung melden, die es nicht gibt."""

    def setUp(self):
        super().setUp()
        self.preview_url = reverse("composer:preview", kwargs={"workspace_id": self.workspace.id})

    def _vorschau(self, **felder):
        daten = {
            "title": "Karussell",
            "caption": BASISTEXT,
            "selected_accounts": str(self.bluesky.id),
            "_autosave_post_id": str(self.post.id),
        }
        daten.update(felder)
        return self.client.post(self.preview_url, data=daten)

    def test_ohne_feld_zaehlt_die_gespeicherte_kurzfassung(self):
        antwort = self._vorschau()

        self.assertEqual(antwort.status_code, 200)
        vorschau = antwort.context["previews"][0]
        self.assertEqual(vorschau["caption"], KURZFASSUNG)
        self.assertEqual(vorschau["char_count"], len(KURZFASSUNG))
        self.assertFalse(vorschau["is_over_limit"])

    def test_ohne_feld_zaehlt_der_gespeicherte_titel(self):
        vorschau = self._vorschau().context["previews"][0]

        self.assertEqual(vorschau["title"], "Kurzer Titel")

    def test_das_formular_gewinnt_wenn_es_das_feld_traegt(self):
        getippt = "Gerade getippte Fassung."
        vorschau = self._vorschau(**{f"override_caption_{self.bluesky.id}": getippt}).context["previews"][0]

        self.assertEqual(vorschau["caption"], getippt)
        self.assertEqual(vorschau["char_count"], len(getippt))

    def test_leeres_feld_faellt_bewusst_auf_den_basistext_zurueck(self):
        # Der Nutzer hat die Fassung entfernt – dann ist die Überschreitung
        # echt und die Warnung richtig.
        vorschau = self._vorschau(**{f"override_caption_{self.bluesky.id}": ""}).context["previews"][0]

        self.assertEqual(vorschau["caption"], BASISTEXT)
        self.assertTrue(vorschau["is_over_limit"])

    def test_ohne_beitrag_bleibt_es_beim_basistext(self):
        # Ein noch nie gespeicherter Entwurf hat nichts, worauf man
        # zurückfallen könnte.
        vorschau = self._vorschau(_autosave_post_id="").context["previews"][0]

        self.assertEqual(vorschau["caption"], BASISTEXT)

    def test_unbrauchbare_beitrags_kennung_bricht_die_vorschau_nicht(self):
        antwort = self._vorschau(_autosave_post_id="kein-uuid")

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["previews"][0]["caption"], BASISTEXT)

    def test_fremder_beitrag_liefert_keine_fassung(self):
        fremd_org = Organization.objects.create(name="Fremd")
        fremd_ws = Workspace.objects.create(organization=fremd_org, name="Fremd")
        fremd_post = Post.objects.create(workspace=fremd_ws, author=self.user, caption="fremd")

        vorschau = self._vorschau(_autosave_post_id=str(fremd_post.id)).context["previews"][0]

        self.assertEqual(vorschau["caption"], BASISTEXT)
