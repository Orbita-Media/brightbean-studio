"""Abzeichen und Detailblock der Bildprüfung (Mediathek, Composer).

``{% bildpruefung_abzeichen asset "thumb" %}`` zeigt den Stand eines Bildes oder
Videos als klickbares Abzeichen; ein Klick öffnet den Dialog
(``media_library/_bildpruefung_dialog.html``, einmal in ``base.html``) mit
Befunden, Prüfer und – für Owner/Admin – „Von Hand freigeben“.
``{% bildpruefung_block asset %}`` ist der Abschnitt im Detailpanel.

Der Stand wird je Request und Asset nur einmal ausgewertet; gerechnet wird der
sha256 nur, wenn eine Prüfung vorliegt und kein gemerkter Wert zur Datei passt.
"""

from __future__ import annotations

import json
from datetime import UTC
from zoneinfo import ZoneInfo

from django import template
from django.urls import reverse
from django.utils import timezone

from apps.media_library import bildpruefung

register = template.Library()

#: Bilder, GIFs und Videos gehen durch die Bildprüfung.
PRUEF_TYPEN = ("image", "gif", "video")

#: Kurzwort für das kleine Abzeichen (Composer-Vorschaubild).
KURZ = {
    bildpruefung.BESTANDEN: "bestanden",
    bildpruefung.DURCHGEFALLEN: "durchgefallen",
    bildpruefung.VON_HAND_FREIGEGEBEN: "von Hand",
    bildpruefung.OFFEN: "offen",
}

#: Formen des Abzeichens: thumb (Composer), karte (Raster), zeile (Liste, Detail).
FORMEN = ("thumb", "karte", "zeile")

BERLIN = ZoneInfo("Europe/Berlin")


def _zeit(stand: bildpruefung.Pruefstand) -> str:
    dt = stand.geprueft_am_dt
    if dt is None:
        return stand.geprueft_am
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=UTC)
    return timezone.localtime(dt, BERLIN).strftime("%d.%m.%Y, %H:%M Uhr")


def _darf(request, workspace, asset) -> bool:
    """Owner/Admin-Prüfung, je Request und Asset-Art (eigen/geteilt) einmal."""
    if request is None or workspace is None:
        return False
    cache = request.__dict__.setdefault("_bildpruefung_darf", {})
    schluessel = (str(workspace.id), asset.workspace_id is None)
    if schluessel not in cache:
        cache[schluessel] = bildpruefung.darf_von_hand_freigeben(request.user, workspace, asset)
    return cache[schluessel]


def bildpruefung_daten(context, asset) -> dict | None:
    """Alles, was Abzeichen, Block und Dialog brauchen; None für Dokumente u. Ä."""
    if asset is None or getattr(asset, "media_type", None) not in PRUEF_TYPEN:
        return None
    request = context.get("request")
    cache = request.__dict__.setdefault("_bildpruefung_stand", {}) if request is not None else {}
    stand = cache.get(asset.pk)
    if stand is None:
        stand = bildpruefung.pruefstand(asset)
        cache[asset.pk] = stand

    workspace = context.get("workspace")
    if workspace is not None and not bildpruefung.gehoert_zum_workspace(asset, workspace):
        workspace = None
    darf = _darf(request, workspace, asset)
    url = (
        reverse(
            "media_library:asset_bildpruefung_freigeben",
            kwargs={"workspace_id": workspace.id, "asset_id": asset.id},
        )
        if darf
        else ""
    )
    titel = f"Bildprüfung: {stand.text}"
    zeit = _zeit(stand) if stand.geprueft_am else ""
    dialog = {
        "id": str(asset.id),
        "dateiname": asset.filename,
        "status": stand.status,
        "titel": titel,
        "statustext": stand.text,
        "grund": stand.grund_text,
        "befunde": stand.befunde,
        "pruefer": stand.pruefer,
        "geprueft_am": zeit,
        "notiz": stand.notiz,
        "modelle": stand.modelle,
        "freigegeben": stand.freigegeben,
        "darf": darf,
        "url": url,
    }
    hinweis = titel
    if stand.durchgefallen and stand.befunde:
        hinweis += " – " + "; ".join(stand.befunde)
    elif stand.offen and stand.grund_text:
        hinweis += " – " + stand.grund_text
    return {
        "id": str(asset.id),
        "status": stand.status,
        "stand": stand,
        "titel": titel,
        "kurz": KURZ.get(stand.status, stand.text),
        "hinweis": hinweis,
        "zeit": zeit,
        "darf": darf,
        "url": url,
        "json": json.dumps(dialog, ensure_ascii=False),
    }


@register.inclusion_tag("media_library/_bildpruefung_abzeichen.html", takes_context=True)
def bildpruefung_abzeichen(context, asset, form="karte"):
    """Abzeichen in den Formen ``thumb``, ``karte`` oder ``zeile``."""
    return {"bp": bildpruefung_daten(context, asset), "form": form if form in FORMEN else "karte"}


@register.inclusion_tag("media_library/_bildpruefung_block.html", takes_context=True)
def bildpruefung_block(context, asset):
    """Abschnitt „Bildprüfung“ im Detailpanel der Mediathek."""
    return {"bp": bildpruefung_daten(context, asset), "asset": asset}
