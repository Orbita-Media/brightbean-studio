"""Tests für den Weg zu den Seiten über die Business-Portfolios.

Hintergrund ist der zweite Fall vom 06.08.2026: Wählt der Nutzer im Dialog
"Alle aktuellen und zukünftigen Seiten", bleiben die ``granular_scopes`` des
Tokens leer. Der Weg über die Seitenkennungen im Token hat dann nichts mehr,
wonach er fragen könnte – dieser hier holt die Kennungen aus dem Portfolio.
"""

from unittest.mock import MagicMock

from providers.exceptions import APIError
from providers.meta_business import (
    MAX_PAGES,
    SOURCE_BUSINESS_PORTFOLIOS,
    business_portfolio_ids,
    pages_from_business_portfolios,
    pages_when_me_accounts_is_empty,
)
from providers.meta_pages import SOURCE_ME_ACCOUNTS, SOURCE_TOKEN_SCOPES

BASE_URL = "https://graph.facebook.com/v25.0"

BUSINESS = "1789004618296789"
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


def _no_token_in_granular_scopes():
    """Das Token der Option "alle": Berechtigung erteilt, kein Ziel genannt."""
    return {
        "data": {
            "app_id": "1062167552935661",
            "user_id": "1069610232678392",
            "is_valid": True,
            "granular_scopes": [
                {"scope": "pages_show_list", "target_ids": []},
                {"scope": "pages_read_engagement", "target_ids": []},
            ],
        }
    }


def test_pages_come_from_the_portfolio_when_the_token_names_none():
    """Der eigentliche Fix: leeres Token, Seiten trotzdem über das Portfolio."""
    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS, "name": "Orbita Media"}]},
            f"/{BUSINESS}/owned_pages": {
                "data": [
                    {
                        "id": PAGE_B,
                        "name": "Orbita Media Verlag",
                        "access_token": "seiten-schlüssel-b",
                        "category": "Publisher",
                        "instagram_business_account": {"id": IG_ACCOUNT, "username": "orbitamedia_verlag"},
                    }
                ]
            },
            f"/{BUSINESS}/client_pages": {"data": []},
        }
    )

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert [page["id"] for page in pages] == [PAGE_B]
    assert pages[0]["instagram_business_account"]["id"] == IG_ACCOUNT
    assert pages[0]["access_token"] == "seiten-schlüssel-b"


def test_owned_and_client_pages_are_both_read_and_deduplicated():
    """Betreute Seiten zählen genauso – dieselbe Seite aber nur einmal."""
    page_b = {"id": PAGE_B, "name": "Orbita Media Verlag", "access_token": "b"}
    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": {"data": [{"id": PAGE_A, "name": "Orbita Media", "access_token": "a"}, page_b]},
            f"/{BUSINESS}/client_pages": {"data": [page_b]},
        }
    )

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert [page["id"] for page in pages] == [PAGE_A, PAGE_B]


def test_a_missing_permission_stays_quiet():
    """Ohne ``business_management`` antwortet der Graph mit Fehler 200.

    Das ist der Normalfall für jeden Nutzer ohne Rolle in der App und darf
    weder eine Ausnahme werfen noch den Anmeldevorgang aufhalten.
    """
    request_fn = _graph_stub(
        {
            "/me/businesses": {
                "error": {
                    "code": 200,
                    "type": "OAuthException",
                    "message": "(#200) Requires business_management permission to manage the object",
                }
            }
        }
    )

    assert (
        pages_from_business_portfolios(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
            field_sets=FIELD_SETS,
        )
        == []
    )
    assert request_fn.call_count == 1, "ohne Portfolio wird keine Seite abgefragt"


def test_a_failing_portfolio_call_does_not_raise():
    request_fn = _graph_stub({"/me/businesses": APIError("Facebook API error 190: token expired", status_code=400)})

    assert (
        business_portfolio_ids(
            request_fn,
            base_url=BASE_URL,
            access_token="user-token",
        )
        == []
    )


def test_one_failing_portfolio_does_not_take_the_others_with_it():
    """Ein Portfolio ohne Auskunft darf das andere nicht mitnehmen."""
    other = "2200000000000000"
    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}, {"id": other}]},
            f"/{BUSINESS}/owned_pages": APIError("Facebook API error 10: not allowed", status_code=403),
            f"/{BUSINESS}/client_pages": APIError("Facebook API error 10: not allowed", status_code=403),
            f"/{other}/owned_pages": {"data": [{"id": PAGE_A, "name": "Orbita Media", "access_token": "a"}]},
            f"/{other}/client_pages": {"data": []},
        }
    )

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert [page["id"] for page in pages] == [PAGE_A]


def test_the_field_ladder_is_walked_from_wide_to_narrow():
    """Kennt die Graph-Version ein Feld an dieser Verbindung nicht, greift die nächste Liste."""
    used: list[str] = []

    def _owned(kwargs):
        fields = kwargs["params"]["fields"]
        used.append(fields)
        if "connected_instagram_account" in fields:
            return {"error": {"code": 100, "message": "nonexisting field on Page"}}
        return {"data": [{"id": PAGE_A, "name": "Orbita Media", "access_token": "a"}]}

    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": _owned,
            f"/{BUSINESS}/client_pages": {"data": []},
        }
    )

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert used[0] == FIELD_SETS[0], "die ausführlichste Feldliste kommt zuerst"
    assert used[1] == FIELD_SETS[1]
    assert [page["id"] for page in pages] == [PAGE_A]


def test_a_missing_page_access_token_is_fetched_separately():
    """Die Portfolio-Verbindungen geben den Seitenschlüssel nicht zuverlässig heraus."""

    def _page(kwargs):
        assert kwargs["params"]["fields"] == "access_token"
        return {"id": PAGE_A, "access_token": "nachgereichter-schlüssel"}

    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": {"data": [{"id": PAGE_A, "name": "Orbita Media"}]},
            f"/{BUSINESS}/client_pages": {"data": []},
            f"/{PAGE_A}": _page,
        }
    )

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert pages[0]["access_token"] == "nachgereichter-schlüssel"


def test_the_number_of_pages_is_capped():
    """Ein Portfolio mit sehr vielen Seiten löst keine Abfragelawine aus."""
    many = [{"id": str(3000 + index), "name": f"Seite {index}", "access_token": "x"} for index in range(MAX_PAGES + 10)]
    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": {"data": many},
            f"/{BUSINESS}/client_pages": {"data": []},
        }
    )

    pages = pages_from_business_portfolios(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert len(pages) == MAX_PAGES


def test_no_token_ever_reaches_a_log_line(caplog):
    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": {"data": [{"id": PAGE_A, "name": "Orbita Media", "access_token": "EAAgeheim"}]},
            f"/{BUSINESS}/client_pages": {"data": []},
        }
    )

    with caplog.at_level("DEBUG"):
        pages_from_business_portfolios(
            request_fn,
            base_url=BASE_URL,
            access_token="EAAnutzertoken",
            field_sets=FIELD_SETS,
        )

    assert "EAAgeheim" not in caplog.text
    assert "EAAnutzertoken" not in caplog.text


# ---------------------------------------------------------------------------
# Die Reihenfolge der beiden Ausweichwege
# ---------------------------------------------------------------------------


def test_the_token_scopes_come_first_and_stop_the_search():
    """Nennt das Token Seiten, bleibt das Portfolio unbehelligt.

    Der Weg über das Token kommt mit den ohnehin erteilten Berechtigungen aus;
    der über das Portfolio braucht eine weitere. Deshalb diese Reihenfolge.
    """
    request_fn = _graph_stub(
        {
            "/debug_token": {
                "data": {"granular_scopes": [{"scope": "pages_show_list", "target_ids": [PAGE_A]}]},
            },
            f"/{PAGE_A}": {"id": PAGE_A, "name": "Orbita Media", "access_token": "a"},
        }
    )

    pages, source = pages_when_me_accounts_is_empty(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
        app_id="app",
        app_secret="secret",
    )

    assert [page["id"] for page in pages] == [PAGE_A]
    assert source == SOURCE_TOKEN_SCOPES
    assert not any("/me/businesses" in str(call) for call in request_fn.call_args_list)


def test_the_portfolio_takes_over_when_the_token_names_nothing():
    """Der Fall "alle Seiten": leeres Token, Portfolio liefert."""
    request_fn = _graph_stub(
        {
            "/debug_token": _no_token_in_granular_scopes(),
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": {"data": [{"id": PAGE_B, "name": "Orbita Media Verlag", "access_token": "b"}]},
            f"/{BUSINESS}/client_pages": {"data": []},
        }
    )

    pages, source = pages_when_me_accounts_is_empty(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
        app_id="app",
        app_secret="secret",
    )

    assert [page["id"] for page in pages] == [PAGE_B]
    assert source == SOURCE_BUSINESS_PORTFOLIOS


def test_nothing_anywhere_reports_the_original_source():
    """Hilft kein Weg, bleibt es bei der Sammelabfrage als Quelle."""
    request_fn = _graph_stub(
        {
            "/debug_token": _no_token_in_granular_scopes(),
            "/me/businesses": {"data": []},
        }
    )

    pages, source = pages_when_me_accounts_is_empty(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
        app_id="app",
        app_secret="secret",
    )

    assert pages == []
    assert source == SOURCE_ME_ACCOUNTS


def test_without_app_credentials_the_portfolio_is_still_tried():
    """Der Portfolio-Weg braucht kein App-Geheimnis, nur das Nutzertoken.

    Ohne App-Kennung entfällt der Weg über ``/debug_token`` – der über das
    Portfolio nicht. Sonst bliebe genau der Fall ungelöst, für den er da ist.
    """
    request_fn = _graph_stub(
        {
            "/me/businesses": {"data": [{"id": BUSINESS}]},
            f"/{BUSINESS}/owned_pages": {"data": [{"id": PAGE_A, "name": "Orbita Media", "access_token": "a"}]},
            f"/{BUSINESS}/client_pages": {"data": []},
        }
    )

    pages, source = pages_when_me_accounts_is_empty(
        request_fn,
        base_url=BASE_URL,
        access_token="user-token",
        field_sets=FIELD_SETS,
    )

    assert [page["id"] for page in pages] == [PAGE_A]
    assert source == SOURCE_BUSINESS_PORTFOLIOS
