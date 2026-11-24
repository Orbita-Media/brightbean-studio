"""Plattform-Drossel: Meta-Limits einhalten statt in sie hineinzulaufen.

Meta meldet Drosselung nicht mit 429, sondern mit HTTP 400/403 und einem
Fehlercode (4 App-Limit, 17 Nutzer-Limit, 32 Seiten-Limit, 613 Aufrufrate,
80001 ff. Business-Use-Case-Limits). providers.base wandelt solche Antworten
in RateLimitError und meldet sie hierher (hart). Zusätzlich meldet es eine
Auslastung ab AUSLASTUNG_GRENZE Prozent aus den Headern x-app-usage und
x-business-use-case-usage (weich), damit Hintergrundabrufe pausieren, bevor
das Limit erreicht ist und Beiträge scheitern.

Leser:
- Auswertung, Postfach, Zustandsprüfung: aktiv(platform) -> überspringen.
- Publisher: hart_bis(platform) -> Beitrag zurückhalten, ohne Versuch zu verbrauchen.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

# Facebook und Instagram (Facebook-Login) teilen sich die App „Orbita Social“,
# Threads hat eine eigene App, Instagram Login läuft über graph.instagram.com.
HOST_JE_PLATTFORM = {
    "facebook": "graph.facebook.com",
    "instagram": "graph.facebook.com",
    "threads": "graph.threads.net",
    "instagram_login": "graph.instagram.com",
}
META_HOSTS = frozenset(HOST_JE_PLATTFORM.values())

HART_STANDARD_S = 3600  # Metas App-Limit ist ein gleitendes Stundenfenster
WEICH_STANDARD_S = 900
HOECHSTENS_S = 6 * 3600


def host_fuer(platform: str) -> str | None:
    return HOST_JE_PLATTFORM.get(platform)


def setzen(host: str, sekunden: int | float, *, hart: bool, grund: str = "") -> None:
    """Drossel für host setzen oder verlängern (nie verkürzen, weich wird nie über hart geschrieben)."""
    from .models import Drossel

    sekunden = max(60, min(int(sekunden or 0) or (HART_STANDARD_S if hart else WEICH_STANDARD_S), HOECHSTENS_S))
    jetzt = timezone.now()
    bis = jetzt + timedelta(seconds=sekunden)
    try:
        alt = Drossel.objects.filter(host=host).first()
        if alt and alt.bis > jetzt:
            if alt.bis >= bis and (alt.hart or not hart):
                return
            hart = hart or alt.hart
            bis = max(bis, alt.bis)
        Drossel.objects.update_or_create(host=host, defaults={"bis": bis, "hart": hart, "grund": grund[:500]})
        logger.warning(
            "Drossel %s bis %s (%s): %s",
            host,
            bis.isoformat(timespec="minutes"),
            "hart" if hart else "weich",
            grund[:200],
        )
    except Exception:  # noqa: BLE001 - die Drossel ist Vorsorge, ein DB-Fehler darf keinen Abruf brechen
        logger.exception("Drossel für %s konnte nicht gesetzt werden", host)


def _gueltig(platform: str):
    from .models import Drossel

    host = host_fuer(platform)
    if not host:
        return None
    try:
        return Drossel.objects.filter(host=host, bis__gt=timezone.now()).first()
    except Exception:  # noqa: BLE001
        logger.exception("Drossel für %s nicht lesbar", host)
        return None


def aktiv(platform: str) -> bool:
    """Hintergrundabrufe für diese Plattform jetzt auslassen (weich oder hart gedrosselt)."""
    return _gueltig(platform) is not None


def hart_bis(platform: str):
    """Ende einer harten Drossel (Plattform hat schon abgelehnt) oder None."""
    d = _gueltig(platform)
    return d.bis if d is not None and d.hart else None


def hook(host: str, sekunden: int | float, hart: bool, grund: str) -> None:
    """Rückruf für providers.base (eingehängt in PublisherConfig.ready)."""
    if host in META_HOSTS:
        setzen(host, sekunden, hart=hart, grund=grund)
