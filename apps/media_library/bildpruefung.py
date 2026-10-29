"""Bildprüfung: kein Bild und kein Video geht ohne bestandene Prüfung raus.

Vertrag mit der Bildwache (Social-Agent im Dirigent) und alle Regeln:
``docs/BILDPRUEFUNG.md``. Kurz:

* ``MediaAsset.bildpruefung`` (JSON, null = ungeprüft) mit ``status``
  (bestanden | durchgefallen | von_hand_freigegeben), ``sha256`` der geprüften
  Datei, ``pruefer``, ``modelle``, ``befunde``, ``geprueft_am``, ``notiz``.
* Gültig ist eine Prüfung nur, wenn ihr ``sha256`` zur aktuellen Datei passt.
  Eine neue Datei oder Version macht sie damit automatisch ungültig.
* Der Publisher sperrt ein gültig durchgefallenes Medium immer; ungeprüfte
  Medien hält er nur zurück, wenn der Schalter ``bildpruefung_pflicht`` an
  ist (``pflicht_an``).
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

BESTANDEN = "bestanden"
DURCHGEFALLEN = "durchgefallen"
VON_HAND_FREIGEGEBEN = "von_hand_freigegeben"
OFFEN = "offen"

ALLE_STATUS = (BESTANDEN, DURCHGEFALLEN, VON_HAND_FREIGEGEBEN)
FREIGABE_STATUS = (BESTANDEN, VON_HAND_FREIGEGEBEN)

#: Grund, warum ein Medium als offen gilt.
GRUND_FEHLT = "fehlt"
GRUND_VERALTET = "veraltet"  # Prüfung gehört zu einer anderen Datei (sha256 weicht ab)
GRUND_DATEI_FEHLT = "datei_fehlt"

STATUS_TEXT = {
    BESTANDEN: "bestanden",
    DURCHGEFALLEN: "durchgefallen",
    VON_HAND_FREIGEGEBEN: "von Hand freigegeben",
    OFFEN: "offen",
}

#: Schlüssel in ``PlatformPost.platform_extra``, deren Asset als Titelbild mit
#: veröffentlicht wird (Reel-/Video-Titelbild, Pinterest-Videocover).
COVER_SCHLUESSEL = ("thumbnail_asset_id", "cover_image_asset_id")

#: PlatformPost-Status, deren Medien noch veröffentlicht werden können.
#: Weiter als der Vertrag (scheduled/draft): auch alles in Freigabe und
#: angehaltene Beiträge, die wieder eingeplant werden können.
OFFENE_BEITRAGS_STATUS = (
    "draft",
    "pending_review",
    "pending_client",
    "approved",
    "changes_requested",
    "on_hold",
    "scheduled",
)

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# sha256 der Datei
# ---------------------------------------------------------------------------


def _dateiname(asset) -> str:
    return getattr(asset.file, "name", "") or ""


def datei_sha256(asset, *, frisch: bool = False) -> str | None:
    """sha256 (hex) der aktuellen Datei des Assets, None ohne lesbare Datei.

    Zwischengespeichert in ``asset.sha256`` für den Dateinamen in
    ``asset.sha256_datei``. Zeigt ``file`` auf eine andere Datei (Upload einer
    neuen Version, Bearbeitung), wird neu gerechnet. ``frisch=True`` rechnet
    in jedem Fall neu (Speichern einer Prüfung).
    """
    name = _dateiname(asset)
    if not name:
        return None
    if not frisch:
        gemerkt = gemerkter_sha256(asset)
        if gemerkt:
            return gemerkt
    digest = hashlib.sha256()
    try:
        with asset.file.open("rb") as src:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                digest.update(chunk)
    except (OSError, ValueError) as exc:
        logger.warning("sha256 für MediaAsset %s nicht lesbar: %s", asset.pk, exc)
        return None
    wert = digest.hexdigest()
    sha_merken(asset, wert)
    return wert


def sha_merken(asset, wert: str) -> None:
    """Merkt den sha256 der aktuellen Datei am Asset (ohne ``updated_at`` zu ändern)."""
    name = _dateiname(asset)
    if asset.sha256 == wert and asset.sha256_datei == name:
        return
    asset.sha256 = wert
    asset.sha256_datei = name
    if asset.pk:
        type(asset).all_objects.filter(pk=asset.pk).update(sha256=wert, sha256_datei=name)


def gemerkter_sha256(asset) -> str | None:
    """Der zwischengespeicherte sha256, wenn er zur aktuellen Datei gehört (ohne zu rechnen)."""
    name = _dateiname(asset)
    if asset.sha256 and name and asset.sha256_datei == name:
        return asset.sha256
    return None


# ---------------------------------------------------------------------------
# Stand einer Prüfung
# ---------------------------------------------------------------------------


@dataclass
class Pruefstand:
    """Ausgewerteter Stand für ein Asset.

    ``status`` ist bestanden, durchgefallen, von_hand_freigegeben oder offen.
    Bei offen sagt ``grund``, warum (fehlt, veraltet, datei_fehlt).
    """

    status: str
    grund: str | None = None
    befunde: list[str] = field(default_factory=list)
    pruefer: str = ""
    geprueft_am: str = ""
    notiz: str = ""
    modelle: list[str] = field(default_factory=list)

    @property
    def freigegeben(self) -> bool:
        return self.status in FREIGABE_STATUS

    @property
    def durchgefallen(self) -> bool:
        return self.status == DURCHGEFALLEN

    @property
    def offen(self) -> bool:
        return self.status == OFFEN

    @property
    def text(self) -> str:
        return STATUS_TEXT.get(self.status, self.status)

    @property
    def grund_text(self) -> str:
        if self.grund == GRUND_VERALTET:
            return "Die Prüfung gehört zu einer älteren Fassung der Datei."
        if self.grund == GRUND_DATEI_FEHLT:
            return "Die Datei ist nicht lesbar."
        if self.grund == GRUND_FEHLT:
            return "Noch nicht geprüft."
        return ""

    @property
    def geprueft_am_dt(self) -> datetime | None:
        return parse_datetime(self.geprueft_am) if self.geprueft_am else None


def pruefstand(asset, *, sha: str | None = None, frisch: bool = False) -> Pruefstand:
    """Wertet ``asset.bildpruefung`` gegen die aktuelle Datei aus.

    Ohne Prüfung wird nicht gerechnet (offen/fehlt). Mit Prüfung wird der
    sha256 der Datei gebraucht: übergeben (``sha``), zwischengespeichert oder
    gerechnet.
    """
    daten = asset.bildpruefung
    if not isinstance(daten, dict) or daten.get("status") not in ALLE_STATUS:
        return Pruefstand(status=OFFEN, grund=GRUND_FEHLT)
    gemeinsam = {
        "befunde": [str(b) for b in daten.get("befunde") or []],
        "pruefer": str(daten.get("pruefer") or ""),
        "geprueft_am": str(daten.get("geprueft_am") or ""),
        "notiz": str(daten.get("notiz") or ""),
        "modelle": [str(m) for m in daten.get("modelle") or []],
    }
    aktuell = sha or datei_sha256(asset, frisch=frisch)
    if aktuell is None:
        return Pruefstand(status=OFFEN, grund=GRUND_DATEI_FEHLT, **gemeinsam)
    if str(daten.get("sha256") or "").lower() != aktuell:
        return Pruefstand(status=OFFEN, grund=GRUND_VERALTET, **gemeinsam)
    return Pruefstand(status=daten["status"], **gemeinsam)


# ---------------------------------------------------------------------------
# Schalter
# ---------------------------------------------------------------------------


def pflicht_an() -> bool:
    """Werden ungeprüfte Medien zurückgehalten? (Schalter in der DB, Standard AUS)."""
    from apps.publisher.models import Schalter

    return Schalter.ist_an(Schalter.BILDPRUEFUNG_PFLICHT)


def pflicht_setzen(an: bool, *, von: str = "") -> None:
    from apps.publisher.models import Schalter

    Schalter.setzen(Schalter.BILDPRUEFUNG_PFLICHT, an, von=von)


# ---------------------------------------------------------------------------
# Prüfung speichern
# ---------------------------------------------------------------------------


class UngueltigePruefung(ValueError):  # noqa: N818 – Name aus dem Vertrag
    """Das übergebene Prüfobjekt verletzt den Vertrag (422)."""


class ShaAbweichung(Exception):  # noqa: N818 – Name aus dem Vertrag
    """Der sha256 der Prüfung passt nicht zur aktuellen Datei (409)."""

    def __init__(self, gemeldet: str, aktuell: str | None):
        self.gemeldet = gemeldet
        self.aktuell = aktuell
        super().__init__(
            f"sha256 {gemeldet} passt nicht zur aktuellen Datei"
            + (f" ({aktuell})." if aktuell else " (Datei nicht lesbar).")
            + " Die Datei wurde seit dem Herunterladen ersetzt oder es wurde eine andere Datei geprüft."
        )


def _liste(wert, feld: str) -> list[str]:
    if wert is None:
        return []
    if not isinstance(wert, list) or not all(isinstance(x, str) for x in wert):
        raise UngueltigePruefung(f"„{feld}“ muss eine Liste von Texten sein.")
    return [x.strip() for x in wert if x.strip()]


def pruefung_bereinigen(daten: dict) -> dict:
    """Prüft ein Prüfobjekt gegen den Vertrag und gibt die gespeicherte Form zurück."""
    if not isinstance(daten, dict):
        raise UngueltigePruefung("Die Prüfung muss ein JSON-Objekt sein.")
    status = daten.get("status")
    if status not in ALLE_STATUS:
        raise UngueltigePruefung(f"„status“ muss einer von {', '.join(ALLE_STATUS)} sein.")
    sha = str(daten.get("sha256") or "").strip().lower()
    if not _SHA_RE.match(sha):
        raise UngueltigePruefung("„sha256“ muss der sha256 der Datei sein (64 Hex-Zeichen).")
    pruefer = str(daten.get("pruefer") or "").strip()
    if not pruefer:
        raise UngueltigePruefung("„pruefer“ fehlt (wer oder was hat geprüft).")
    befunde = _liste(daten.get("befunde"), "befunde")
    modelle = _liste(daten.get("modelle"), "modelle")
    notiz = daten.get("notiz") or ""
    if not isinstance(notiz, str):
        raise UngueltigePruefung("„notiz“ muss ein Text sein.")
    notiz = notiz.strip()
    if status == DURCHGEFALLEN and not befunde:
        raise UngueltigePruefung("Bei „durchgefallen“ gehört mindestens ein Befund in „befunde“.")
    if status == VON_HAND_FREIGEGEBEN and not notiz:
        raise UngueltigePruefung("Eine Freigabe von Hand braucht eine Begründung in „notiz“.")
    geprueft_am = daten.get("geprueft_am")
    if geprueft_am:
        dt = parse_datetime(str(geprueft_am))
        if dt is None:
            raise UngueltigePruefung("„geprueft_am“ muss ein ISO-Zeitpunkt sein.")
        if timezone.is_naive(dt):
            dt = dt.replace(tzinfo=UTC)
    else:
        dt = timezone.now()
    return {
        "status": status,
        "sha256": sha,
        "pruefer": pruefer[:255],
        "modelle": modelle,
        "befunde": befunde,
        "geprueft_am": dt.isoformat(),
        "notiz": notiz,
    }


def pruefung_speichern(asset, daten: dict) -> dict:
    """Speichert eine Prüfung; der sha256 wird gegen die Datei frisch abgeglichen.

    Wirft ``UngueltigePruefung`` (Vertrag verletzt) oder ``ShaAbweichung``.
    """
    sauber = pruefung_bereinigen(daten)
    aktuell = datei_sha256(asset, frisch=True)
    if aktuell != sauber["sha256"]:
        raise ShaAbweichung(sauber["sha256"], aktuell)
    asset.bildpruefung = sauber
    type(asset).all_objects.filter(pk=asset.pk).update(bildpruefung=sauber)
    logger.info(
        "Bildprüfung für MediaAsset %s gespeichert: %s (%s)",
        asset.pk,
        sauber["status"],
        sauber["pruefer"],
    )
    return sauber


def von_hand_freigeben(asset, *, benutzer, notiz: str) -> dict:
    """Freigabe von Hand durch Owner/Admin (UI). Prüfer ist der Benutzer."""
    aktuell = datei_sha256(asset, frisch=True)
    if aktuell is None:
        raise UngueltigePruefung("Die Datei ist nicht lesbar, eine Freigabe ist nicht möglich.")
    vorher = asset.bildpruefung if isinstance(asset.bildpruefung, dict) else {}
    email = getattr(benutzer, "email", "") or str(benutzer)
    name = getattr(benutzer, "name", "") or ""
    return pruefung_speichern(
        asset,
        {
            "status": VON_HAND_FREIGEGEBEN,
            "sha256": aktuell,
            "pruefer": f"{name} <{email}>" if name else email,
            "modelle": [],
            # Die Befunde einer vorherigen Prüfung derselben Datei bleiben
            # sichtbar: wer von Hand freigibt, überstimmt sie bewusst.
            "befunde": [str(b) for b in vorher.get("befunde") or []] if vorher.get("sha256") == aktuell else [],
            "notiz": notiz,
        },
    )


def darf_von_hand_freigeben(user, workspace, asset=None) -> bool:
    """Owner/Admin der Organisation oder Owner des Workspace.

    Ein org-geteiltes Asset (``asset.workspace_id is None``) gilt für alle
    Workspaces der Organisation; das darf nur Owner/Admin der Organisation
    von Hand freigeben, nicht der Owner eines einzelnen Workspace.
    """
    if user is None or not getattr(user, "is_authenticated", False) or workspace is None:
        return False
    from apps.members.models import OrgMembership, WorkspaceMembership

    if OrgMembership.objects.filter(
        user=user,
        organization_id=workspace.organization_id,
        org_role__in=[OrgMembership.OrgRole.OWNER, OrgMembership.OrgRole.ADMIN],
    ).exists():
        return True
    if asset is not None and asset.workspace_id is None:
        return False
    return WorkspaceMembership.objects.filter(
        user=user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    ).exists()


# ---------------------------------------------------------------------------
# Was ein Beitrag veröffentlicht, was noch offen ist
# ---------------------------------------------------------------------------


def cover_asset_ids(platform_extra) -> list[str]:
    """IDs der Titelbild-Assets, die mit dem Beitrag veröffentlicht werden."""
    extra = platform_extra or {}
    return [str(extra[k]) for k in COVER_SCHLUESSEL if extra.get(k)]


def gehoert_zum_workspace(asset, workspace) -> bool:
    """Darf das Asset an Beiträgen dieses Workspace hängen? (eigenes oder org-geteiltes)"""
    if asset.workspace_id is not None:
        return asset.workspace_id == workspace.id
    return asset.organization_id is not None and asset.organization_id == workspace.organization_id


def haengt_an_beitraegen(asset, workspace) -> bool:
    """Hängt das Asset (Anhang oder Titelbild) an einem noch veröffentlichbaren Beitrag des Workspace?"""
    from django.db.models import Q

    from apps.composer.models import PlatformPost, PostMedia

    if not gehoert_zum_workspace(asset, workspace):
        return False
    offen = PlatformPost.objects.filter(status__in=OFFENE_BEITRAGS_STATUS, post__workspace=workspace)
    if PostMedia.objects.filter(media_asset=asset, post__in=offen.values("post_id")).exists():
        return True
    kennung = str(asset.id)
    return offen.filter(
        Q(platform_extra__thumbnail_asset_id=kennung) | Q(platform_extra__cover_image_asset_id=kennung)
    ).exists()


def uuids(werte) -> list[uuid.UUID]:
    ergebnis = []
    for wert in werte:
        try:
            ergebnis.append(uuid.UUID(str(wert)))
        except ValueError:
            continue
    return ergebnis


def _sortkey(termin):
    return (termin is None, termin or datetime.max.replace(tzinfo=UTC))


def offene_medien(*, workspace_ids=None, bis: datetime | None = None, limit: int | None = None) -> dict:
    """Medien an noch nicht veröffentlichten Beiträgen ohne gültige Prüfung.

    Rückgabe ``{"anzahl": int, "durchgefallen": [eintrag, ...], "eintraege": [...]}``;
    die Einträge sind nach nächstem Termin sortiert (ohne Termin zuletzt).
    Mit ``bis`` zählen nur Beiträge mit Termin bis dahin. Jeder Eintrag:
    ``asset`` (MediaAsset), ``stand`` (Pruefstand), ``termin`` (nächster),
    ``plattformen``, ``workspace_id``, ``beitragstext``, ``beitraege``.
    """
    from django.db.models.functions import Coalesce

    from apps.composer.models import PlatformPost, PostMedia
    from apps.media_library.models import MediaAsset

    pps = (
        PlatformPost.objects.filter(status__in=OFFENE_BEITRAGS_STATUS)
        .select_related("post__workspace", "social_account")
        .annotate(termin=Coalesce("scheduled_at", "post__scheduled_at"))
    )
    if workspace_ids is not None:
        pps = pps.filter(post__workspace_id__in=list(workspace_ids))
    if bis is not None:
        pps = pps.filter(termin__lte=bis)

    beitraege: dict = {}
    asset_beitraege: dict = {}
    for pp in pps:
        eintrag = beitraege.setdefault(pp.post_id, {"post": pp.post, "termin": None, "plattformen": set()})
        if pp.termin and (eintrag["termin"] is None or pp.termin < eintrag["termin"]):
            eintrag["termin"] = pp.termin
        eintrag["plattformen"].add(pp.social_account.platform)
        for cid in cover_asset_ids(pp.platform_extra):
            asset_beitraege.setdefault(cid, set()).add(pp.post_id)

    for pm in PostMedia.objects.filter(post_id__in=list(beitraege)).values("post_id", "media_asset_id"):
        asset_beitraege.setdefault(str(pm["media_asset_id"]), set()).add(pm["post_id"])

    eintraege = []
    durchgefallen = []
    for asset in MediaAsset.all_objects.filter(id__in=uuids(asset_beitraege)):
        # Mandantengrenze: ein Asset zählt nur für Beiträge seines eigenen
        # Workspace oder, wenn org-geteilt, derselben Organisation. Eine
        # fremde ID in platform_extra macht so kein fremdes Medium sichtbar.
        posts = sorted(
            (
                beitraege[pid]
                for pid in asset_beitraege.get(str(asset.id), ())
                if gehoert_zum_workspace(asset, beitraege[pid]["post"].workspace)
            ),
            key=lambda b: _sortkey(b["termin"]),
        )
        if not posts:
            continue
        stand = pruefstand(asset)
        if not (stand.offen or stand.durchgefallen):
            continue
        erster = posts[0]
        eintrag = {
            "asset": asset,
            "stand": stand,
            "termin": erster["termin"],
            "plattformen": sorted({p for b in posts for p in b["plattformen"]}),
            "workspace_id": erster["post"].workspace_id,
            "beitragstext": (erster["post"].caption or "")[:160],
            "beitraege": [
                {"post_id": b["post"].id, "termin": b["termin"], "plattformen": sorted(b["plattformen"])} for b in posts
            ],
        }
        (durchgefallen if stand.durchgefallen else eintraege).append(eintrag)

    def ordnen(liste):
        liste.sort(key=lambda e: (*_sortkey(e["termin"]), str(e["asset"].id)))

    ordnen(eintraege)
    ordnen(durchgefallen)
    anzahl = len(eintraege)
    if limit is not None:
        eintraege = eintraege[:limit]
    return {"anzahl": anzahl, "durchgefallen": durchgefallen, "eintraege": eintraege}


def fehlende_hashes_nachrechnen(asset_ids) -> int:
    """Rechnet den sha256 für Assets ohne gemerkten Wert (Hintergrund, Kommando)."""
    from apps.media_library.models import MediaAsset

    gerechnet = 0
    for asset in MediaAsset.all_objects.filter(id__in=uuids(asset_ids)):
        if gemerkter_sha256(asset) is None and datei_sha256(asset) is not None:
            gerechnet += 1
    return gerechnet
