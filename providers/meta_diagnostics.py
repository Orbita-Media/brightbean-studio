"""Diagnose für Meta-Anbindungen: was gibt der Graph wirklich zurück?

Wird vom OAuth-Rückweg aufgerufen, wenn eine Facebook- oder Instagram-Anbindung
keine einzige verwendbare Seite gefunden hat. Dahinter stecken Fälle, die von
aussen identisch aussehen und deshalb bisher nicht zu unterscheiden waren:

1. ``/me/accounts`` liefert gar keine Seite – dann fehlt ``pages_show_list``
   oder im Anmeldedialog wurde keine Seite ausgewählt.
2. Seiten kommen, aber keine trägt ein Instagram-Konto – dann ist die
   Verknüpfung im Sinne des Graph nicht gesetzt, egal was das
   Business-Portfolio anzeigt.
3. Seiten kommen ohne Instagram-Konto, weil dem Nutzer die Rolle fehlt, der
   Meta das verknüpfte Konto überhaupt zeigt (``tasks``, siehe
   ``pages_without_content_role``). Sieht aus wie Fall 2, ist es aber nicht.
4. Eine Seite trägt ein Konto, das sich trotzdem nicht anbieten lässt – dann
   liegt es am Konto (privat statt professionell), nicht an der Verknüpfung.
5. Der Aufruf läuft in ein Fehlerobjekt – dann sagt der Fehlercode es direkt.

Fall 3 und 4 unterscheidet nicht ``classify``, sondern der Aufrufer anhand von
``pages_without_content_role`` bzw. daran, dass trotz Verknüpfung nichts
angeboten werden konnte.

Es landet KEIN Zugangstoken im Ergebnis, weder ganz noch gekürzt: nur
Kennungen, Namen, Berechtigungsnamen und Fehlerobjekte. Das Ergebnis ist
bewusst JSON-serialisierbar, damit der Aufrufer es in eine einzige Logzeile
schreiben kann.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Siehe ``redact``: Nutzer- und Seitentoken von Meta beginnen mit ``EAA``,
# App-Token sind ``<app-id>|<app-secret>``.
_TOKEN_PATTERN = re.compile(r"EAA[A-Za-z0-9_\-]{10,}")
_APP_TOKEN_PATTERN = re.compile(r"\b\d{10,}\|[A-Za-z0-9]{16,}\b")

# Verdikte, die ``classify`` liefert. Der Aufrufer entscheidet daran, welche
# Meldung der Nutzer sieht – deshalb sind es Konstanten und keine losen Strings.
VERDICT_NO_PAGES = "no_pages"
VERDICT_PAGES_WITHOUT_INSTAGRAM = "pages_without_instagram"
VERDICT_PAGES_WITH_INSTAGRAM = "pages_with_instagram"
VERDICT_UNKNOWN = "unknown"

# Feldlisten in absteigender Ausführlichkeit. Ein Feldname, den die angefragte
# Graph-Version nicht kennt, lässt den GANZEN Aufruf scheitern (Fehler 100,
# "nonexisting field"). Darum wird von oben nach unten probiert, bis eine Liste
# durchgeht – eine Diagnose, die selbst am Feldnamen scheitert, wäre wertlos.
PAGE_FIELD_SETS = (
    "id,name,category,tasks,instagram_business_account{id,username},connected_instagram_account{id,username}",
    "id,name,category,tasks,instagram_business_account{id,username}",
    "id,name",
)

# Mehr Seiten hat kein Konto, das hier durchläuft; die Grenze verhindert nur,
# dass eine Diagnose zur Datensammlung ausartet.
PAGE_LIMIT = 100

# Seitenrollen, die zum Verwalten von Inhalten berechtigen. Ohne mindestens eine
# davon blendet Meta das verknüpfte Instagram-Konto aus, siehe
# ``pages_without_content_role``.
PAGE_TASKS_FOR_CONTENT = ("MANAGE", "CREATE_CONTENT")


def collect_diagnostics(
    request_fn: Callable,
    *,
    base_url: str,
    access_token: str,
    app_id: str = "",
    app_secret: str = "",
) -> dict:
    """Sammelt Berechtigungen, Token-Umfang und Seitenliste zu einem Zugang.

    ``request_fn`` ist die ``_request``-Methode eines Providers, damit die
    Diagnose dieselbe Fehlerbehandlung und dieselben Zeitgrenzen nutzt wie der
    Rest des Providers. Jeder Schritt ist einzeln abgesichert: fällt einer aus,
    steht das im Ergebnis und die übrigen laufen weiter.
    """
    diagnostics: dict = {"errors": []}

    permissions = _safe(diagnostics, "permissions", lambda: _permissions(request_fn, base_url, access_token))
    if permissions is not None:
        diagnostics["permissions"] = permissions

    if app_id and app_secret:
        token_info = _safe(
            diagnostics,
            "debug_token",
            lambda: _token_info(request_fn, base_url, access_token, app_id, app_secret),
        )
        if token_info is not None:
            diagnostics["token"] = token_info

    pages = _safe(diagnostics, "pages", lambda: _pages(request_fn, base_url, access_token))
    if pages is not None:
        diagnostics["pages"] = pages

    diagnostics["verdict"] = classify(diagnostics)
    return diagnostics


def classify(diagnostics: dict) -> str:
    """Leitet aus den Rohdaten den einen Fall ab, der die Meldung bestimmt."""
    pages = diagnostics.get("pages")
    if not isinstance(pages, dict):
        return VERDICT_UNKNOWN
    items = pages.get("items") or []
    if not items:
        return VERDICT_NO_PAGES
    for page in items:
        if page.get("instagram_business_account") or page.get("connected_instagram_account"):
            return VERDICT_PAGES_WITH_INSTAGRAM
    return VERDICT_PAGES_WITHOUT_INSTAGRAM


def page_names(diagnostics: dict, limit: int = 3) -> list[str]:
    """Namen der gefundenen Seiten, für die Meldung an den Nutzer."""
    pages = diagnostics.get("pages")
    if not isinstance(pages, dict):
        return []
    names = [str(page.get("name") or page.get("id") or "") for page in pages.get("items") or []]
    return [name for name in names if name][:limit]


def pages_without_content_role(diagnostics: dict, limit: int = 3) -> list[str]:
    """Seiten, auf denen der Nutzer keine Rolle mit Inhaltsrechten hat.

    Das Feld ``instagram_business_account`` verlangt laut Referenz "a User access
    token from a User who is able to perform appropriate tasks on the Page"
    (Graph API Reference, Page). Fehlt diese Rolle, bleibt das Feld leer – das
    sieht von aussen aus wie eine fehlende Verknüpfung, ist aber eine fehlende
    Berechtigung. Nur ``tasks`` unterscheidet die beiden Fälle.

    Kamen die Seiten über eine Feldliste ohne ``tasks`` (Rückfall, siehe
    ``PAGE_FIELD_SETS``), wird nichts behauptet: Eine leere Liste heisst dann
    "nicht erhoben" und nicht "keine Rechte".
    """
    pages = diagnostics.get("pages")
    if not isinstance(pages, dict) or "tasks" not in str(pages.get("fields") or ""):
        return []
    without: list[str] = []
    for page in pages.get("items") or []:
        tasks = {str(task).upper() for task in page.get("tasks") or []}
        if not tasks & set(PAGE_TASKS_FOR_CONTENT):
            without.append(str(page.get("name") or page.get("id") or ""))
    return [name for name in without if name][:limit]


def instagram_links(diagnostics: dict, limit: int = 3) -> list[tuple[str, str]]:
    """Seiten, an denen laut Graph ein Instagram-Konto hängt: (Seitenname, Konto-Kennung).

    Wird für den dritten Fall gebraucht: Die Verknüpfung IST gesetzt, das Konto
    liess sich trotzdem nicht anbieten. Dann liegt es am Konto selbst (nicht
    professionell) und nicht an der Verknüpfung – eine Meldung über fehlende
    Verknüpfungen würde in die Irre führen.
    """
    pages = diagnostics.get("pages")
    if not isinstance(pages, dict):
        return []
    found: list[tuple[str, str]] = []
    for page in pages.get("items") or []:
        ig_id = page.get("instagram_business_account") or page.get("connected_instagram_account") or ""
        if ig_id:
            found.append((str(page.get("name") or page.get("id") or ""), str(ig_id)))
    return found[:limit]


# ---------------------------------------------------------------------------
# Einzelschritte
# ---------------------------------------------------------------------------


def _permissions(request_fn: Callable, base_url: str, access_token: str) -> dict:
    data = request_fn("GET", f"{base_url}/me/permissions", access_token=access_token).json()
    entries = data.get("data") or []
    granted = sorted(str(e.get("permission", "")) for e in entries if e.get("status") == "granted")
    declined = sorted(str(e.get("permission", "")) for e in entries if e.get("status") != "granted")
    return {"granted": granted, "declined": declined}


def _token_info(request_fn: Callable, base_url: str, access_token: str, app_id: str, app_secret: str) -> dict:
    """Liest den Umfang des Tokens über ``/debug_token``.

    Der Wert steckt in ``granular_scopes``: Beim Business-Login wählt der Nutzer
    im Dialog einzelne Seiten und Instagram-Konten aus, und nur diese stehen
    dort als ``target_ids``. Eine erteilte Berechtigung ohne passende Ziel-ID
    ist der Unterschied zwischen "hat zugestimmt" und "hat auch etwas
    ausgewählt" – ohne dieses Feld ist er nicht sichtbar.

    Der App-Token (``app_id|app_secret``) geht als Bearer-Kopfzeile raus, nicht
    als Adressparameter, und wird nirgends protokolliert.
    """
    data = (
        request_fn(
            "GET",
            f"{base_url}/debug_token",
            access_token=f"{app_id}|{app_secret}",
            params={"input_token": access_token},
        )
        .json()
        .get("data")
        or {}
    )
    granular: dict = {}
    for entry in data.get("granular_scopes") or []:
        if isinstance(entry, dict):
            granular[str(entry.get("scope", ""))] = [str(t) for t in entry.get("target_ids") or []]
    return {
        "app_id": str(data.get("app_id", "")),
        "user_id": str(data.get("user_id", "")),
        "type": str(data.get("type", "")),
        "is_valid": data.get("is_valid"),
        "expires_at": data.get("expires_at"),
        "scopes": sorted(str(s) for s in data.get("scopes") or []),
        "granular_scopes": granular,
    }


def _pages(request_fn: Callable, base_url: str, access_token: str) -> dict:
    last_error: Exception | None = None
    for fields in PAGE_FIELD_SETS:
        try:
            payload = request_fn(
                "GET",
                f"{base_url}/me/accounts",
                access_token=access_token,
                params={"fields": fields, "limit": PAGE_LIMIT},
            ).json()
        except Exception as exc:  # noqa: BLE001 - jeder Fehler ist hier ein Befund
            last_error = exc
            continue

        items = []
        for page in payload.get("data") or []:
            items.append(
                {
                    "id": str(page.get("id", "")),
                    "name": str(page.get("name", "")),
                    "category": str(page.get("category", "")),
                    "tasks": [str(t) for t in page.get("tasks") or []],
                    "instagram_business_account": str((page.get("instagram_business_account") or {}).get("id", "")),
                    "connected_instagram_account": str((page.get("connected_instagram_account") or {}).get("id", "")),
                }
            )
        return {
            "fields": fields,
            "count": len(items),
            "has_next_page": bool((payload.get("paging") or {}).get("next")),
            "response_error": _describe(payload.get("error")) if payload.get("error") else None,
            "items": items,
        }

    raise last_error if last_error else RuntimeError("Seitenabfrage lieferte kein Ergebnis")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _safe(diagnostics: dict, step: str, fn: Callable):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - ein gescheiterter Schritt ist selbst ein Befund
        diagnostics["errors"].append({"step": step, "error": _describe(exc)})
        logger.warning("Meta-Diagnose: Schritt %s fehlgeschlagen: %s", step, exc)
        return None


def _describe(error) -> dict:
    """Bringt ein Graph-Fehlerobjekt oder eine Ausnahme auf eine kurze Form."""
    if isinstance(error, dict):
        payload = error.get("error") if isinstance(error.get("error"), dict) else error
        return {
            "code": payload.get("code"),
            "subcode": payload.get("error_subcode"),
            "type": str(payload.get("type", "")),
            "message": redact(str(payload.get("message", "")))[:300],
        }

    raw = getattr(error, "raw_response", None)
    if isinstance(raw, dict) and raw:
        described = _describe(raw)
        if any(described.values()):
            return described
    return {"code": None, "subcode": None, "type": type(error).__name__, "message": redact(str(error))[:300]}


def redact(text: str) -> str:
    """Entfernt alles, was wie ein Zugangstoken aussieht, aus einem Text.

    Der Fehlertext eines Providers enthält den Anfang der Antwort im Klartext
    (``providers.base.SocialProvider._request``). Die Diagnose fragt zwar keine
    Token-Felder ab, aber eine Fehlermeldung ist der eine Ort, an dem doch eine
    Antwort mitkommt, die nie jemand geprüft hat. Deshalb wird hier stumpf
    gelöscht statt darauf zu vertrauen: Meta-Nutzer- und Seitentoken beginnen
    mit ``EAA``, App-Token haben die Form ``<app-id>|<app-secret>``.
    """
    cleaned = _TOKEN_PATTERN.sub("[entfernt]", text or "")
    return _APP_TOKEN_PATTERN.sub("[entfernt]", cleaned)
