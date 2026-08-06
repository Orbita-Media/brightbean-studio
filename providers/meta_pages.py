"""Seiten eines Meta-Zugangs finden, auch wenn ``/me/accounts`` leer bleibt.

Der dokumentierte Weg zu den Seiten eines Nutzers ist ``GET /me/accounts``. Die
Referenz beschreibt die Verbindung als "The Pages and Places this person
manages" und meint damit die klassische Seitenrolle. Gehört eine Seite einem
Business-Portfolio und ist der Zugriff dort zugewiesen, kann dieselbe Abfrage
eine leere Liste liefern, obwohl der Nutzer die Seite im Anmeldedialog
ausdrücklich freigegeben hat.

Genau dieser Fall trat am 06.08.2026 bei ``@orbitamedia_verlag`` auf. Das
Protokoll war eindeutig: ``pages.count = 0``, gleichzeitig nannte
``granular_scopes`` des Tokens zwei Seitenkennungen für ``pages_show_list`` und
``pages_read_engagement``. Der Nutzer hatte also ausgewählt, der Graph gab die
Auswahl nur nicht über die Sammelabfrage heraus.

Der Ausweichweg nutzt genau diese Auswahl: Die Kennungen stehen im Token, und
jede Seite lässt sich einzeln unter ``GET /{page-id}`` abfragen. Das ist keine
Notlösung, sondern der Weg, den die Referenz für eine einzelne Seite
beschreibt, inklusive des Seitenschlüssels über ``fields=access_token``.

Es landet KEIN Zugangstoken in einer Protokollzeile: Geloggt werden nur
Kennungen, Namen und Fehlerbeschreibungen.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

# Woher die Seiten stammen. Steht so im Protokoll, damit beim nächsten Konto
# sofort sichtbar ist, welcher Weg gegriffen hat.
SOURCE_ME_ACCOUNTS = "me_accounts"
SOURCE_TOKEN_SCOPES = "granular_scopes"

# Berechtigungen, deren ``target_ids`` Seitenkennungen sind. Die
# ``instagram_*``-Berechtigungen tragen an derselben Stelle die Kennung des
# Instagram-Kontos, nicht die einer Seite, und bleiben deshalb aussen vor.
PAGE_ID_SCOPES = ("pages_show_list", "pages_read_engagement")

# Obergrenze für den Ausweichweg. Jede Seite kostet eine eigene Abfrage; mehr
# Seiten als das hat kein Zugang, der hier durchläuft.
MAX_PAGE_LOOKUPS = 25


def pages_from_token_scopes(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    field_sets: Sequence[str],
    app_id: str = "",
    app_secret: str = "",
    label: str = "Meta",
) -> list[dict]:
    """Seiten über die im Token vermerkten Kennungen holen.

    Erst ``/debug_token`` nach den ausgewählten Seitenkennungen fragen, dann
    jede Seite einzeln abfragen. Ohne App-Kennung und App-Geheimnis gibt es
    keinen App-Token und damit keine Auskunft über das Token; dann bleibt es
    bei der leeren Liste.
    """
    if not (app_id and app_secret):
        logger.warning(
            "%s: /me/accounts lieferte keine Seite, und ohne App-Kennung lässt sich das Token "
            "nicht auslesen. Der Ausweichweg entfällt.",
            label,
        )
        return []

    page_ids = page_ids_from_token(
        request_fn,
        base_url=base_url,
        access_token=access_token,
        app_id=app_id,
        app_secret=app_secret,
        label=label,
    )
    if not page_ids:
        logger.warning(
            "%s: /me/accounts lieferte keine Seite, und das Token nennt auch keine freigegebene "
            "Seite. Im Anmeldedialog wurde im Schritt Seiten nichts angehakt.",
            label,
        )
        return []

    pages = pages_by_id(
        request_fn,
        base_url=base_url,
        access_token=access_token,
        page_ids=page_ids,
        field_sets=field_sets,
        label=label,
    )
    logger.info(
        "%s: %d von %d im Token genannten Seite(n) über den Ausweichweg %s geladen (/me/accounts war leer)",
        label,
        len(pages),
        len(page_ids),
        SOURCE_TOKEN_SCOPES,
    )
    return pages


def page_ids_from_token(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    app_id: str,
    app_secret: str,
    label: str = "Meta",
) -> list[str]:
    """Die im Anmeldedialog freigegebenen Seitenkennungen aus dem Token lesen.

    Vereinigt ``pages_show_list`` und ``pages_read_engagement`` und entdoppelt
    dabei, behält aber die Reihenfolge des Tokens. Der App-Token geht als
    Kopfzeile raus, nicht als Adressparameter, und wird nirgends protokolliert.
    """
    try:
        payload = request_fn(
            "GET",
            f"{base_url}/debug_token",
            access_token=f"{app_id}|{app_secret}",
            params={"input_token": access_token},
        ).json()
    except Exception as exc:  # noqa: BLE001 - ein gescheiterter Schritt darf nichts mitreissen
        logger.warning("%s: Token lässt sich nicht auslesen (%s)", label, exc)
        return []

    data = payload.get("data") or {}
    page_ids: list[str] = []
    for entry in data.get("granular_scopes") or []:
        if not isinstance(entry, dict) or str(entry.get("scope", "")) not in PAGE_ID_SCOPES:
            continue
        for target in entry.get("target_ids") or []:
            target_id = str(target)
            if target_id and target_id not in page_ids:
                page_ids.append(target_id)

    if len(page_ids) > MAX_PAGE_LOOKUPS:
        logger.warning(
            "%s: Token nennt %d Seiten, geladen werden die ersten %d",
            label,
            len(page_ids),
            MAX_PAGE_LOOKUPS,
        )
        page_ids = page_ids[:MAX_PAGE_LOOKUPS]
    return page_ids


def pages_by_id(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    page_ids: Sequence[str],
    field_sets: Sequence[str],
    label: str = "Meta",
) -> list[dict]:
    """Jede Seite einzeln abfragen und die Antworten sammeln.

    Scheitert eine Seite, wird sie übersprungen und protokolliert. Der ganze
    Vorgang bricht nicht ab: Eine Seite, auf die der Zugang nicht reicht, darf
    die andere nicht mitnehmen.

    ``field_sets`` ist eine Leiter von der ausführlichsten zur schmalsten
    Feldliste. Ein Feldname, den die angefragte Graph-Version nicht kennt, lässt
    den ganzen Aufruf scheitern (Fehler 100, "nonexisting field"), deshalb wird
    von oben nach unten probiert.
    """
    pages: list[dict] = []
    for page_id in page_ids:
        page = _page_by_id(
            request_fn,
            base_url=base_url,
            access_token=access_token,
            page_id=page_id,
            field_sets=field_sets,
            label=label,
        )
        if page:
            pages.append(page)
    return pages


def _page_by_id(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    page_id: str,
    field_sets: Sequence[str],
    label: str,
) -> dict | None:
    last_error: Exception | None = None
    for fields in field_sets:
        try:
            payload = request_fn(
                "GET",
                f"{base_url}/{page_id}",
                access_token=access_token,
                params={"fields": fields},
            ).json()
        except Exception as exc:  # noqa: BLE001 - nächste Feldliste versuchen
            last_error = exc
            continue

        if isinstance(payload.get("error"), dict):
            last_error = RuntimeError(str(payload["error"].get("message", "unbekannter Fehler")))
            continue
        if not payload.get("id"):
            last_error = RuntimeError("Antwort ohne Kennung")
            continue

        if "access_token" in fields and not payload.get("access_token"):
            token = _page_access_token(
                request_fn,
                base_url=base_url,
                access_token=access_token,
                page_id=page_id,
                label=label,
            )
            if token:
                payload["access_token"] = token
        return payload

    logger.warning("%s: Seite %s lässt sich nicht laden (%s)", label, page_id, last_error)
    return None


def _page_access_token(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    page_id: str,
    label: str,
) -> str:
    """Den Seitenschlüssel notfalls einzeln nachfordern.

    Ein Seitenschlüssel ist die Voraussetzung zum Veröffentlichen. Kommt er in
    der gemeinsamen Feldliste nicht mit, wird er hier gezielt angefordert. Der
    Wert wird zurückgegeben, aber nie protokolliert.
    """
    try:
        payload = request_fn(
            "GET",
            f"{base_url}/{page_id}",
            access_token=access_token,
            params={"fields": "access_token"},
        ).json()
    except Exception as exc:  # noqa: BLE001 - ohne Seitenschlüssel geht es notfalls weiter
        logger.warning("%s: Seitenschlüssel für %s nicht abrufbar (%s)", label, page_id, exc)
        return ""

    token = str(payload.get("access_token") or "")
    if not token:
        logger.warning(
            "%s: Seite %s liefert keinen Seitenschlüssel. Veröffentlichen wird daran scheitern.",
            label,
            page_id,
        )
    return token
