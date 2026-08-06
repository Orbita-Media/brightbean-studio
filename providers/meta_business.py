"""Seiten über die Business-Portfolios eines Zugangs finden.

Der dritte und letzte Weg zu den Seiten eines Meta-Zugangs, gebraucht für genau
einen Fall: Der Nutzer hat im Anmeldedialog **"Alle aktuellen und zukünftigen
Seiten"** gewählt.

Warum das ein eigener Fall ist: Bei "Nur aktuelle Seiten auswählen" stehen die
angehakten Seitenkennungen in den ``granular_scopes`` des Tokens, und
``providers/meta_pages.py`` holt jede Seite einzeln darüber. Bei "alle" bleiben
diese Listen **leer** (belegt am 06.08.2026: ``"pages_show_list": []``) – die
Zustimmung ist dann nicht auf einzelne Ziele beschränkt, und deshalb vermerkt
Meta auch keine. Zusammen mit einem ``/me/accounts``, das bei Seiten aus einem
Business-Portfolio ohnehin nichts herausgibt, bleibt kein einziger Anker übrig:
Das Token dürfte jede Seite abfragen, aber niemand kennt eine Kennung.

Diesen Anker liefert das Portfolio selbst:

1. ``GET /me/businesses`` nennt die Portfolios, auf die der Nutzer Zugriff hat.
2. ``GET /{business-id}/owned_pages`` und ``GET /{business-id}/client_pages``
   nennen deren Seiten – owned für die eigenen, client für die betreuten.

Beides verlangt ``business_management``. Diese Berechtigung braucht **keine
App-Überprüfung**, solange der Nutzer eine Rolle in der App hat: "Berechtigungen
mit Standardzugriff können nur von App-Nutzer*innen angefordert werden, die eine
Rolle in der anfordernden App haben", und Business-Apps sind "automatisch für
alle Berechtigungen und Features für Standardzugriff genehmigt" (Graph API,
Zugriffsebenen). Für fremde Nutzer, die später über einen Verbindungslink
kommen, gilt das nicht – für die bleibt "Nur aktuelle Seiten auswählen" der Weg.

Der Facebook-Anbieter fragt ``business_management`` seit jeher an; der
Instagram-Anbieter tut es seit dem 06.08.2026 ebenfalls.

Es landet KEIN Zugangstoken in einer Protokollzeile: Geloggt werden nur
Kennungen, Namen und Fehlerbeschreibungen.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from .meta_pages import (
    SOURCE_ME_ACCOUNTS,
    SOURCE_TOKEN_SCOPES,
    fetch_page_access_token,
    pages_from_token_scopes,
)

logger = logging.getLogger(__name__)

# Woher die Seiten stammen. Steht so im Protokoll, damit beim nächsten Konto
# sofort sichtbar ist, welcher Weg gegriffen hat.
SOURCE_BUSINESS_PORTFOLIOS = "business_portfolios"

# Die beiden Verbindungen, an denen die Seiten eines Portfolios hängen. "owned"
# sind die eigenen, "client" die betreuten – beide zählen.
BUSINESS_PAGE_EDGES = ("owned_pages", "client_pages")

# Obergrenzen. Sie verhindern, dass ein Zugang mit vielen Portfolios eine
# Abfragelawine auslöst; mehr hat kein Zugang, der hier durchläuft.
MAX_BUSINESSES = 10
MAX_PAGES = 50
EDGE_PAGE_LIMIT = 100


def pages_when_me_accounts_is_empty(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    field_sets: Sequence[str],
    app_id: str = "",
    app_secret: str = "",
    label: str = "Meta",
) -> tuple[list[dict], str]:
    """Beide Ausweichwege in der Reihenfolge, in der sie sich lohnen.

    Erst die im Dialog angehakten Seitenkennungen aus dem Token, dann die
    Portfolios. Die Reihenfolge ist nicht beliebig: Der Weg über das Token
    kostet zwei Abfragen und kommt mit den ohnehin erteilten Berechtigungen
    aus, der über die Portfolios braucht ``business_management``.

    Zurück kommt auch die Quelle, damit im Protokoll steht, welcher Weg
    gegriffen hat. Findet keiner etwas, ist die Quelle wieder
    ``me_accounts`` – dann hat schlicht nichts geholfen.
    """
    pages = pages_from_token_scopes(
        request_fn,
        base_url=base_url,
        access_token=access_token,
        field_sets=field_sets,
        app_id=app_id,
        app_secret=app_secret,
        label=label,
    )
    if pages:
        return pages, SOURCE_TOKEN_SCOPES

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=base_url,
        access_token=access_token,
        field_sets=field_sets,
        label=label,
    )
    if pages:
        return pages, SOURCE_BUSINESS_PORTFOLIOS
    return [], SOURCE_ME_ACCOUNTS


def pages_from_business_portfolios(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    field_sets: Sequence[str],
    label: str = "Meta",
) -> list[dict]:
    """Seiten über die Business-Portfolios des Nutzers holen.

    Läuft nur, wenn weder ``/me/accounts`` noch die Kennungen im Token etwas
    hergeben. Jeder Schritt ist einzeln abgesichert: Ein Portfolio, das keine
    Auskunft gibt, darf die anderen nicht mitnehmen, und fehlt
    ``business_management``, bleibt es still bei der leeren Liste.
    """
    business_ids = business_portfolio_ids(
        request_fn,
        base_url=base_url,
        access_token=access_token,
        label=label,
    )
    if not business_ids:
        return []

    pages: list[dict] = []
    seen: set[str] = set()
    for business_id in business_ids:
        for edge in BUSINESS_PAGE_EDGES:
            for page in _pages_from_edge(
                request_fn,
                base_url=base_url,
                access_token=access_token,
                business_id=business_id,
                edge=edge,
                field_sets=field_sets,
                label=label,
            ):
                page_id = str(page.get("id") or "")
                if not page_id or page_id in seen:
                    continue
                seen.add(page_id)
                pages.append(page)
                if len(pages) >= MAX_PAGES:
                    logger.warning(
                        "%s: Die Portfolios nennen mehr als %d Seiten, weitere werden nicht geladen",
                        label,
                        MAX_PAGES,
                    )
                    return _with_access_tokens(
                        request_fn,
                        base_url=base_url,
                        access_token=access_token,
                        pages=pages,
                        field_sets=field_sets,
                        label=label,
                    )

    pages = _with_access_tokens(
        request_fn,
        base_url=base_url,
        access_token=access_token,
        pages=pages,
        field_sets=field_sets,
        label=label,
    )
    logger.info(
        "%s: %d Seite(n) über den Ausweichweg %s aus %d Portfolio(s) geladen "
        "(/me/accounts und die Kennungen im Token waren leer)",
        label,
        len(pages),
        SOURCE_BUSINESS_PORTFOLIOS,
        len(business_ids),
    )
    return pages


def business_portfolio_ids(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    label: str = "Meta",
) -> list[str]:
    """Die Kennungen der Business-Portfolios lesen, auf die der Zugang reicht.

    Ohne ``business_management`` antwortet der Graph mit Fehler 200. Das ist
    kein Ausnahmefall, sondern der Normalfall für jeden Nutzer ohne Rolle in
    der App – deshalb wird es als Hinweis protokolliert und nicht als Fehler
    weitergereicht.
    """
    try:
        payload = request_fn(
            "GET",
            f"{base_url}/me/businesses",
            access_token=access_token,
            params={"fields": "id,name", "limit": MAX_BUSINESSES},
        ).json()
    except Exception as exc:  # noqa: BLE001 - ein gescheiterter Schritt darf nichts mitreissen
        logger.info("%s: Business-Portfolios nicht abfragbar (%s)", label, exc)
        return []

    if isinstance(payload.get("error"), dict):
        logger.info(
            "%s: Business-Portfolios nicht abfragbar (%s)",
            label,
            payload["error"].get("message", "unbekannter Fehler"),
        )
        return []

    ids: list[str] = []
    for entry in payload.get("data") or []:
        business_id = str((entry or {}).get("id") or "")
        if business_id and business_id not in ids:
            ids.append(business_id)
    if not ids:
        logger.info("%s: Der Zugang gehört zu keinem Business-Portfolio", label)
    return ids[:MAX_BUSINESSES]


def _pages_from_edge(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    business_id: str,
    edge: str,
    field_sets: Sequence[str],
    label: str,
) -> list[dict]:
    """Eine Seitenverbindung eines Portfolios abfragen.

    ``field_sets`` ist dieselbe Leiter von breit nach schmal wie beim Weg über
    die einzelne Seite: Ein Feldname, den die angefragte Graph-Version an dieser
    Verbindung nicht kennt, lässt den ganzen Aufruf scheitern (Fehler 100,
    "nonexisting field").
    """
    last_error: str = ""
    for fields in field_sets:
        try:
            payload = request_fn(
                "GET",
                f"{base_url}/{business_id}/{edge}",
                access_token=access_token,
                params={"fields": fields, "limit": EDGE_PAGE_LIMIT},
            ).json()
        except Exception as exc:  # noqa: BLE001 - nächste Feldliste versuchen
            last_error = str(exc)
            continue

        if isinstance(payload.get("error"), dict):
            last_error = str(payload["error"].get("message", "unbekannter Fehler"))
            continue

        items = [page for page in payload.get("data") or [] if isinstance(page, dict) and page.get("id")]
        if items:
            logger.info("%s: Portfolio %s nennt %d Seite(n) unter %s", label, business_id, len(items), edge)
        return items

    logger.info("%s: %s des Portfolios %s nicht abfragbar (%s)", label, edge, business_id, last_error)
    return []


def _with_access_tokens(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    pages: list[dict],
    field_sets: Sequence[str],
    label: str,
) -> list[dict]:
    """Fehlende Seitenschlüssel nachfordern.

    Die Seitenverbindungen eines Portfolios geben ``access_token`` nicht
    zuverlässig heraus, und ohne Seitenschlüssel kann Facebook nichts
    veröffentlichen. Nachgefordert wird nur, wenn der Schlüssel überhaupt
    verlangt war und trotzdem fehlt.
    """
    if not any("access_token" in fields for fields in field_sets):
        return pages
    for page in pages:
        if page.get("access_token"):
            continue
        token = fetch_page_access_token(
            request_fn,
            base_url=base_url,
            access_token=access_token,
            page_id=str(page.get("id") or ""),
            label=label,
        )
        if token:
            page["access_token"] = token
    return pages
