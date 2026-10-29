"""Publisher-Sperre Bildprüfung (docs/BILDPRUEFUNG.md).

Am 29.10.2026 ging ein KI-Buchbild (Buch auf dem Schüsselrand, Strahl quer
durchs Bild) auf Instagram und Facebook raus. Seitdem gilt vor jedem
Veröffentlichen, je Bild und Video (auch Titelbild):

* bestanden / von Hand freigegeben mit passendem sha256: veröffentlichen,
* gültig durchgefallen: endgültig nicht, immer (auch bei Schalter aus),
* ungeprüft oder sha256 einer älteren Datei: bei Schalter an zurückhalten,
  ohne einen Versuch zu verbrauchen; 6 h nach dem Termin endgültig scheitern.
"""

import hashlib
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone

from apps.media_library import bildpruefung as bp
from apps.publisher.engine import BILDPRUEFUNG_FRIST, BILDPRUEFUNG_WARTEZEIT, MAX_RETRIES
from apps.publisher.models import PublishLog
from apps.publisher.test_outcome_unknown import _FakeProvider, _OutcomeBase
from providers.types import PostType, PublishResult

BILD = b"\x89PNG\r\n\x1a\nbuch-in-szene"
SHA = hashlib.sha256(BILD).hexdigest()


def _ok():
    return PublishResult(platform_post_id="ig-1", url="https://www.instagram.com/p/ig-1/", extra={})


class _VideoProvider(_FakeProvider):
    supported_post_types = [PostType.VIDEO, PostType.SHORT]


class _Base(_OutcomeBase):
    def setUp(self):
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self._media_root)
        media.enable()
        self.addCleanup(media.disable)
        super().setUp()

    def _asset(self, inhalt=BILD, name="buch-in-szene.png", pruefung=None, media_type="image"):
        from apps.media_library.models import MediaAsset

        asset = MediaAsset(
            organization=self.org,
            workspace=self.workspace,
            filename=name,
            media_type=media_type,
            bildpruefung=pruefung,
        )
        asset.file.save(name, ContentFile(inhalt), save=False)
        asset.save()
        return asset

    def _anhängen(self, asset, position=0, post=None):
        from apps.composer.models import PostMedia

        PostMedia.objects.create(post=post or self.post, media_asset=asset, position=position)

    @staticmethod
    def _pruefung(status, sha=SHA, befunde=None, notiz=""):
        return {
            "status": status,
            "sha256": sha,
            "pruefer": "bildwache",
            "modelle": ["claude-opus-5-5"],
            "befunde": befunde or [],
            "geprueft_am": "2026-10-29T22:00:00+00:00",
            "notiz": notiz,
        }

    @staticmethod
    def _pflicht(an=True):
        bp.pflicht_setzen(an, von="test")


class FreigabeTest(_Base):
    def test_bestanden_wird_veröffentlicht(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset(pruefung=self._pruefung(bp.BESTANDEN)))
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(len(provider.publish_calls), 1)

    def test_von_hand_freigegeben_wird_veröffentlicht(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset(pruefung=self._pruefung(bp.VON_HAND_FREIGEGEBEN, notiz="geprüft, passt")))
        pp = self._pp()
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)

    def test_schalter_aus_veröffentlicht_ungeprüfte(self):
        from apps.composer.models import PlatformPost

        self._anhängen(self._asset())
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertFalse(bp.pflicht_an())


class DurchgefallenTest(_Base):
    def test_durchgefallen_scheitert_endgültig_mit_befunden(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        befunde = ["Buch steht auf dem Schüsselrand", "Dressing-Strahl läuft senkrecht durchs Bild"]
        self._anhängen(self._asset(pruefung=self._pruefung(bp.DURCHGEFALLEN, befunde=befunde)))
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertEqual(provider.publish_calls, [])
        self.assertIn("Bildprüfung durchgefallen", pp.publish_error)
        self.assertIn("Buch steht auf dem Schüsselrand", pp.publish_error)
        self.assertIn("Dressing-Strahl", pp.publish_error)
        self.assertEqual(pp.retry_count, 0)
        self.assertIsNone(pp.next_retry_at)

    def test_durchgefallen_sperrt_auch_bei_schalter_aus(self):
        """Hauptsitzung 29.10.: ein gültiges Durchgefallen sperrt immer."""
        from apps.composer.models import PlatformPost

        self.assertFalse(bp.pflicht_an())
        self._anhängen(self._asset(pruefung=self._pruefung(bp.DURCHGEFALLEN, befunde=["Lichtstrahl"])))
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertEqual(provider.publish_calls, [])
        self.assertIn("Lichtstrahl", pp.publish_error)

    def test_durchgefallen_geht_vor_offen(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset(name="a.png"), position=0)
        self._anhängen(
            self._asset(pruefung=self._pruefung(bp.DURCHGEFALLEN, befunde=["Matsch"]), name="b.png"), position=1
        )
        pp = self._pp()
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("Anhang 2 (b.png): Matsch", pp.publish_error)

    def test_durchgefallenes_titelbild_sperrt(self):
        from apps.composer.models import PlatformPost

        cover = self._asset(pruefung=self._pruefung(bp.DURCHGEFALLEN, befunde=["Hand mit sechs Fingern"]))
        self._anhängen(self._asset(name="clip.mp4", media_type="video", pruefung=self._pruefung(bp.BESTANDEN)))
        pp = self._pp(platform_extra={"thumbnail_asset_id": str(cover.id)})
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("Titelbild", pp.publish_error)
        self.assertEqual(provider.publish_calls, [])

    def test_durchgefallen_zu_alter_datei_sperrt_bei_schalter_aus_nicht(self):
        """Nach einem Austausch der Datei gilt das alte Urteil nicht mehr."""
        from apps.composer.models import PlatformPost

        self._anhängen(self._asset(pruefung=self._pruefung(bp.DURCHGEFALLEN, sha="0" * 64, befunde=["alt"])))
        pp = self._pp()
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)


class ZurückhaltenTest(_Base):
    def test_fehlt_hält_zurück_ohne_versuch_zu_verbrauchen(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        vorher = timezone.now()
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 0)
        self.assertEqual(provider.publish_calls, [])
        self.assertIsNone(pp.dispatched_at)
        self.assertGreaterEqual(pp.next_retry_at, vorher + BILDPRUEFUNG_WARTEZEIT)
        self.assertIn("Bildprüfung fehlt", pp.publish_error)
        self.assertIn("10 Minuten", pp.publish_error)
        self.assertIn("buch-in-szene.png", pp.publish_error)
        self.assertEqual(PublishLog.objects.filter(platform_post=pp).count(), 1)

    def test_zurückgehaltener_erstversuch_wartet_auf_next_retry_at(self):
        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)
        # Nächster Takt sofort danach: nichts passiert, kein zweiter Log-Eintrag.
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(PublishLog.objects.filter(platform_post=pp).count(), 1)
        self.assertEqual(provider.publish_calls, [])

    def test_nach_prüfung_geht_der_beitrag_als_erster_versuch_raus(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        asset = self._asset()
        self._anhängen(asset)
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        bp.pruefung_speichern(asset, self._pruefung(bp.BESTANDEN))
        self._age(pp, next_retry_at=timezone.now() - timedelta(seconds=1))
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(pp.retry_count, 0)
        self.assertEqual(len(provider.publish_calls), 1)
        self.assertEqual(pp.publish_error, "")

    def test_zurückhalten_verbraucht_auch_im_retry_keinen_versuch(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp(retry_count=2, next_retry_at=timezone.now() - timedelta(seconds=1))
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 2)

    def test_letzter_versuch_scheitert_nicht_am_zurückhalten(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp(retry_count=MAX_RETRIES, next_retry_at=timezone.now() - timedelta(seconds=1))
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, MAX_RETRIES)

    def test_sha_abweichung_hält_zurück(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset(pruefung=self._pruefung(bp.BESTANDEN, sha="f" * 64)))
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(provider.publish_calls, [])
        self.assertIn("älteren Fassung", pp.publish_error)

    def test_neue_datei_macht_prüfung_ungültig(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        asset = self._asset(pruefung=self._pruefung(bp.BESTANDEN))
        self.assertTrue(asset.bildpruefung_stand.freigegeben)
        asset.file.save("neu.png", ContentFile(b"andere bytes"), save=True)
        self._anhängen(asset)
        pp = self._pp()
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertIn("Bildprüfung fehlt", pp.publish_error)

    def test_ungeprüftes_titelbild_hält_zurück(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        cover = self._asset(name="cover.png")
        pp = self._pp(platform_extra={"thumbnail_asset_id": str(cover.id)})
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertIn("Titelbild (cover.png)", pp.publish_error)

    def test_nur_video_plattform_prüft_nur_was_sie_veröffentlicht(self):
        """YouTube/TikTok lassen Bilder weg; ein ungeprüftes Bild hält dort nicht auf."""
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset(name="bild.png"), position=0)
        self._anhängen(
            self._asset(name="clip.mp4", media_type="video", pruefung=self._pruefung(bp.BESTANDEN)), position=1
        )
        pp = self._pp()
        self._run(_VideoProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)

    def test_sechs_stunden_nach_termin_endgültig(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp(scheduled_at=timezone.now() - BILDPRUEFUNG_FRIST - timedelta(minutes=1))
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.FAILED)
        self.assertIn("6 Stunden nach dem Termin", pp.publish_error)
        self.assertEqual(provider.publish_calls, [])
        self.assertEqual(pp.retry_count, 0)

    def test_kurz_vor_sechs_stunden_noch_zurückhalten(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp(scheduled_at=timezone.now() - BILDPRUEFUNG_FRIST + timedelta(minutes=5))
        self._run(_FakeProvider(publish=[_ok()]))

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)

    def test_schalter_aus_nach_zurückhalten_gibt_frei(self):
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)
        self._pflicht(False)
        self._age(pp, next_retry_at=timezone.now() - timedelta(seconds=1))
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)


class ShaZwischenspeicherTest(_Base):
    def test_sha_wird_gemerkt_und_bei_neuer_datei_neu_gerechnet(self):
        asset = self._asset()
        self.assertEqual(bp.datei_sha256(asset), SHA)
        asset.refresh_from_db()
        self.assertEqual(asset.sha256, SHA)
        self.assertEqual(asset.sha256_datei, asset.file.name)

        asset.file.save("v2.png", ContentFile(b"v2"), save=True)
        self.assertIsNone(bp.gemerkter_sha256(asset))
        self.assertEqual(bp.datei_sha256(asset), hashlib.sha256(b"v2").hexdigest())

    def test_pruefung_speichern_prüft_sha(self):
        asset = self._asset()
        with self.assertRaises(bp.ShaAbweichung) as ctx:
            bp.pruefung_speichern(asset, self._pruefung(bp.BESTANDEN, sha="a" * 64))
        self.assertEqual(ctx.exception.aktuell, SHA)
        asset.refresh_from_db()
        self.assertIsNone(asset.bildpruefung)

    def test_durchgefallen_braucht_befund(self):
        asset = self._asset()
        with self.assertRaises(bp.UngueltigePruefung):
            bp.pruefung_speichern(asset, self._pruefung(bp.DURCHGEFALLEN))


class OffeneMedienTest(_Base):
    def test_liste_enthält_nur_ungültig_geprüfte_nach_termin(self):
        from apps.composer.models import PlatformPost, Post

        zweiter_beitrag = Post.objects.create(workspace=self.workspace, caption="Zweiter")
        self._anhängen(self._asset(name="erster.png"))
        self._pp(scheduled_at=timezone.now() + timedelta(hours=1))

        self._anhängen(self._asset(name="zweiter.png", inhalt=b"anders"), post=zweiter_beitrag)
        PlatformPost.objects.create(
            post=zweiter_beitrag,
            social_account=self.account,
            status=PlatformPost.Status.DRAFT,
            scheduled_at=timezone.now() + timedelta(days=3),
        )
        gut = self._asset(name="gut.png", pruefung=self._pruefung(bp.BESTANDEN))
        self._anhängen(gut, position=1, post=zweiter_beitrag)
        schlecht = self._asset(name="schlecht.png", pruefung=self._pruefung(bp.DURCHGEFALLEN, befunde=["x"]))
        self._anhängen(schlecht, position=2, post=zweiter_beitrag)

        ergebnis = bp.offene_medien(workspace_ids=[self.workspace.id])
        self.assertEqual([e["asset"].filename for e in ergebnis["eintraege"]], ["erster.png", "zweiter.png"])
        self.assertEqual([e["asset"].filename for e in ergebnis["durchgefallen"]], ["schlecht.png"])

        bis = bp.offene_medien(workspace_ids=[self.workspace.id], bis=timezone.now() + timedelta(days=1))
        self.assertEqual([e["asset"].filename for e in bis["eintraege"]], ["erster.png"])

    def test_veröffentlichte_beiträge_zählen_nicht(self):
        from apps.composer.models import PlatformPost

        self._anhängen(self._asset())
        self._pp(status=PlatformPost.Status.PUBLISHED)
        self.assertEqual(bp.offene_medien(workspace_ids=[self.workspace.id])["anzahl"], 0)


@patch("apps.publisher.engine.OUTCOME_LOOKUP_DELAY", 0)
class DoppelpostSchutzBleibtTest(_Base):
    def test_zurückhalten_vor_dem_nachsehen(self):
        """Eine Zeile mit unklarem früherem Versuch wird zurückgehalten, ohne die Markierung zu verlieren."""
        from apps.composer.models import PlatformPost

        self._pflicht()
        self._anhängen(self._asset())
        seit = timezone.now() - timedelta(minutes=20)
        pp = self._pp(retry_count=1, next_retry_at=timezone.now() - timedelta(seconds=1), outcome_unknown_since=seit)
        provider = _FakeProvider(publish=[_ok()], find=[None])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.outcome_unknown_since, seit)
        self.assertEqual(provider.find_calls, [])
        self.assertEqual(provider.publish_calls, [])
