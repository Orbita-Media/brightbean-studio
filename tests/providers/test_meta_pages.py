"""Tests für den Ausweichweg zu den Seiten eines Meta-Zugangs.

Hintergrund ist der Befund vom 06.08.2026: ``/me/accounts`` lieferte null
Seiten, obwohl das Token in ``granular_scopes`` zwei Seitenkennungen nannte.
Die Seiten gehören einem Business-Portfolio; der Nutzer hat dort Zugriff, aber
keine klassische Seitenrolle.
"""

from unittest.mock import MagicMock

import pytest

from providers.exceptions import APIError
from providers.meta_pages import (
    MAX_PAGE_LOOKUPS,
    page_ids_from_token,
    pages_by_id,
    pages_from_token_scopes,
)

BASE_URL = "https://graph.facebook.com/v25.0"

# Noahs echter Fall, gekürzt auf das, was hier zählt.
PAGE_A = "708768612318133"
PAGE_B = "254978271039996"
IG_ACCOUNT = "17841466348000992"

FIELD_SETS = (
    "id,name,access_token,category,instagram_business_account{id,username},connected_instagram_account{id,username}",
    "id,name,access_token,category",
    "id,name",
)


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _debug_token_payload(scopes: dict) -> dict:
    return {
        "data": {
            "app_id": "1062167552935661",
            "user_id": "1069610232678392",
            "type": "USER",
            "is_valid": True,
            "granular_scopes": [{"scope": scope, "target_ids": targets} for scope, targets in scopes.items()],
        }
    }


def _graph_stub(handlers):
    """Antwortet nach Endpunkt, damit die Reihenfolge der Aufrufe egal ist."""

    def _call(method, url, **kwargs):
        for marker, payload in handlers.items():
            if url.endswith(marker):
                value = payload(kwargs) if callable(payload) else payload
                if isinstance(value, Exception):
                    raise value
                return _resp(value)
        raise AssertionError(f"Unerwarteter Aufruf: {url}")

    return MagicMock(side_effect=_call)


def test_pages_come_from_the_ids_in_the_token_when_me_accounts_is_empty():
    """Der eigentliche Fix: Kennungen aus dem Token, jede Seite einzeln geholt."""
    request_fn = _graph_stub(
        {
            "/debug_token": _debug_token_payload(
                {
                    "instagram_basic": [IG_ACCOUNT],
                    "pages_read_engagement": [PAGE_A, PAGE_B],
                    "pages_show_list": [PAGE_A, PAGE_B],
                }
            ),
            f"/{PAGE_A}": {
                "id": PAGE_A,
                "name": "Orbita Media",
                "access_token": "seiten-schlüssel-a",
                "category": "Publisher",
            },
            f"/{PAGE_B}": {
                "id": PAGE_B,
                "name": "Orbita Media Verlag",
                "access_token": "seiten-schlüssel-b",
                "category": "Publisher",
                "instagram_business_account": {"id": IG_ACCOUNT, "username": "orbitamedia_verlag"},
            },
        }
    )

    pages = pages_from_token_scopes(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
        app_id="app",
        app_secret="secret",
    )

    assert [page["id"] for page in pages] == [PAGE_A, PAGE_B]
    assert pages[1]["instagram_business_account"]["id"] == IG_ACCOUNT
    # Der Seitenschlüssel ist der Grund für die ganze Übung: ohne ihn kann der
    # Verteiler nichts veröffentlichen.
    assert pages[0]["access_token"] == "seiten-schlüssel-a"
    assert pages[1]["access_token"] == "seiten-schlüssel-b"


def test_instagram_target_ids_are_not_mistaken_for_pages():
    """Die ``instagram_*``-Berechtigungen tragen die Konto-, nicht die Seitenkennung."""
    request_fn = _graph_stub(
        {
            "/debug_token": _debug_token_payload(
                {
                    "instagram_basic": [IG_ACCOUNT],
                    "instagram_content_publish": [IG_ACCOUNT],
                    "pages_show_list": [PAGE_B],
                }
            ),
        }
    )

    assert page_ids_from_token(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        app_id="app",
        app_secret="secret",
    ) == [PAGE_B]


def test_page_ids_are_deduplicated_and_keep_their_order():
    """Beide Berechtigungen nennen dieselben Seiten – abgefragt wird jede einmal."""
    request_fn = _graph_stub(
        {
            "/debug_token": _debug_token_payload(
                {
                    "pages_show_list": [PAGE_A, PAGE_B],
                    "pages_read_engagement": [PAGE_B, PAGE_A],
                }
            ),
        }
    )

    assert page_ids_from_token(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        app_id="app",
        app_secret="secret",
    ) == [PAGE_A, PAGE_B]


def test_page_lookups_are_capped():
    """Der Ausweichweg kostet eine Abfrage je Seite und bleibt deshalb gedeckelt."""
    many = [str(1000 + index) for index in range(MAX_PAGE_LOOKUPS + 5)]
    request_fn = _graph_stub({"/debug_token": _debug_token_payload({"pages_show_list": many})})

    ids = page_ids_from_token(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        app_id="app",
        app_secret="secret",
    )

    assert ids == many[:MAX_PAGE_LOOKUPS]


def test_the_page_access_token_is_fetched_separately_when_it_is_missing():
    """Kommt der Seitenschlüssel nicht mit, wird er einzeln nachgefordert."""
    calls: list[str] = []

    def _page(kwargs):
        fields = kwargs["params"]["fields"]
        calls.append(fields)
        if fields == "access_token":
            return {"id": PAGE_B, "access_token": "nachgereichter-schlüssel"}
        return {"id": PAGE_B, "name": "Orbita Media Verlag"}

    request_fn = _graph_stub({f"/{PAGE_B}": _page})

    pages = pages_by_id(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        page_ids=[PAGE_B],
        field_sets=FIELD_SETS,
    )

    assert pages[0]["access_token"] == "nachgereichter-schlüssel"
    assert calls[-1] == "access_token", "die Nachforderung fragt genau dieses eine Feld"


def test_a_missing_page_access_token_does_not_drop_the_page():
    """Ohne Seitenschlüssel bleibt die Seite trotzdem in der Liste."""

    def _page(kwargs):
        if kwargs["params"]["fields"] == "access_token":
            return {"id": PAGE_B}
        return {"id": PAGE_B, "name": "Orbita Media Verlag"}

    pages = pages_by_id(
        _graph_stub({f"/{PAGE_B}": _page}),
        base_url=BASE_URL,
        access_token="user-token",
        page_ids=[PAGE_B],
        field_sets=FIELD_SETS,
    )

    assert [page["id"] for page in pages] == [PAGE_B]
    assert not pages[0].get("access_token")


def test_one_failing_page_does_not_take_the_others_with_it():
    """Eine Seite ohne Zugriff wird übersprungen, nicht der ganze Vorgang."""
    request_fn = _graph_stub(
        {
            f"/{PAGE_A}": APIError("Facebook API error 100: Object does not exist", status_code=400),
            f"/{PAGE_B}": {"id": PAGE_B, "name": "Orbita Media Verlag", "access_token": "schlüssel"},
        }
    )

    pages = pages_by_id(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        page_ids=[PAGE_A, PAGE_B],
        field_sets=FIELD_SETS,
    )

    assert [page["id"] for page in pages] == [PAGE_B]


def test_an_error_object_in_a_200_response_counts_as_a_failure():
    """Der Graph antwortet auch mit 200 und Fehlerobjekt – das ist kein Treffer."""
    request_fn = _graph_stub({f"/{PAGE_B}": {"error": {"code": 100, "message": "nonexisting field"}}})

    assert (
        pages_by_id(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
            page_ids=[PAGE_B],
            field_sets=("id,name",),
        )
        == []
    )


def test_the_field_ladder_is_walked_from_wide_to_narrow():
    """Kennt die Graph-Version ein Feld nicht, greift die nächstschmalere Liste."""
    used: list[str] = []

    def _page(kwargs):
        fields = kwargs["params"]["fields"]
        used.append(fields)
        if "connected_instagram_account" in fields:
            return APIError("Facebook API error 100: nonexisting field", status_code=400)
        return {"id": PAGE_B, "name": "Orbita Media Verlag", "access_token": "schlüssel"}

    pages = pages_by_id(
        _graph_stub({f"/{PAGE_B}": _page}),
        base_url=BASE_URL,
        access_token="user-token",
        page_ids=[PAGE_B],
        field_sets=FIELD_SETS,
    )

    assert used[0] == FIELD_SETS[0], "die ausführlichste Feldliste kommt zuerst"
    assert used[1] == FIELD_SETS[1]
    assert pages[0]["id"] == PAGE_B


def test_without_app_credentials_there_is_no_fallback():
    """Ohne App-Token lässt sich das Nutzertoken nicht auslesen."""
    request_fn = MagicMock()

    assert (
        pages_from_token_scopes(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
            field_sets=FIELD_SETS,
        )
        == []
    )
    request_fn.assert_not_called()


def test_a_failing_debug_token_call_stays_quiet():
    """Fällt ``/debug_token`` aus, bleibt es bei der leeren Liste."""
    request_fn = _graph_stub({"/debug_token": APIError("Facebook API error 190: token expired", status_code=400)})

    assert (
        pages_from_token_scopes(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
            field_sets=FIELD_SETS,
            app_id="app",
            app_secret="secret",
        )
        == []
    )


def test_a_token_without_page_targets_leads_to_no_page_lookup():
    """Wurde im Dialog keine Seite angehakt, gibt es nichts nachzuschlagen."""
    request_fn = _graph_stub({"/debug_token": _debug_token_payload({"pages_show_list": []})})

    assert (
        pages_from_token_scopes(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
            field_sets=FIELD_SETS,
            app_id="app",
            app_secret="secret",
        )
        == []
    )
    assert request_fn.call_count == 1, "nur die Token-Abfrage, keine Seitenabfrage"


@pytest.mark.parametrize("app_id,app_secret", [("app", ""), ("", "secret")])
def test_half_credentials_are_treated_as_none(app_id, app_secret):
    request_fn = MagicMock()

    assert (
        pages_from_token_scopes(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
            field_sets=FIELD_SETS,
            app_id=app_id,
            app_secret=app_secret,
        )
        == []
    )
    request_fn.assert_not_called()


def test_the_app_token_never_reaches_a_log_line(caplog):
    """Der App-Token geht als Kopfzeile raus und darf nirgends auftauchen."""
    request_fn = _graph_stub({"/debug_token": _debug_token_payload({"pages_show_list": []})})

    with caplog.at_level("DEBUG"):
        pages_from_token_scopes(
            request_fn,
            base_url=BASE_URL,
            access_token="EAAgeheimesnutzertoken",
            field_sets=FIELD_SETS,
            app_id="app",
            app_secret="streng-geheim",
        )

    assert "streng-geheim" not in caplog.text
    assert "EAAgeheimesnutzertoken" not in caplog.text
    # Der App-Token gehört in die Kopfzeile, nicht in die Adresse.
    _, kwargs = request_fn.call_args
    assert kwargs["access_token"] == "app|streng-geheim"
    assert kwargs["params"] == {"input_token": "EAAgeheimesnutzertoken"}
