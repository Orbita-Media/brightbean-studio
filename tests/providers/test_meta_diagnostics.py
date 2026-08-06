"""Tests für die Meta-Diagnose im OAuth-Rückweg."""

from unittest.mock import MagicMock

from providers.exceptions import APIError
from providers.instagram import InstagramProvider
from providers.meta_diagnostics import (
    VERDICT_NO_PAGES,
    VERDICT_PAGES_WITH_INSTAGRAM,
    VERDICT_PAGES_WITHOUT_INSTAGRAM,
    classify,
    collect_diagnostics,
    instagram_links,
    page_names,
    pages_without_content_role,
    redact,
)


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _request_stub(responses):
    """Antwortet je nach Endpunkt, damit die Reihenfolge der Aufrufe egal ist."""

    def _call(method, url, **kwargs):
        for marker, payload in responses.items():
            if marker in url:
                if isinstance(payload, Exception):
                    raise payload
                return _resp(payload)
        raise AssertionError(f"Unerwarteter Aufruf: {url}")

    return MagicMock(side_effect=_call)


def test_collect_diagnostics_reports_pages_without_instagram():
    request_fn = _request_stub(
        {
            "/me/permissions": {
                "data": [
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "instagram_basic", "status": "granted"},
                    {"permission": "read_insights", "status": "declined"},
                ]
            },
            "/debug_token": {
                "data": {
                    "app_id": "1062167552935661",
                    "user_id": "123",
                    "type": "USER",
                    "is_valid": True,
                    "scopes": ["pages_show_list", "instagram_basic"],
                    "granular_scopes": [{"scope": "pages_show_list", "target_ids": ["page-1"]}],
                }
            },
            "/me/accounts": {
                "data": [
                    {"id": "page-1", "name": "Orbita Media Verlag", "category": "Publisher", "tasks": ["MANAGE"]},
                ]
            },
        }
    )

    diagnostics = collect_diagnostics(
        request_fn,
        base_url="https://graph.facebook.com/v25.0",
        access_token="user-token",
        app_id="app",
        app_secret="secret",
    )

    assert diagnostics["verdict"] == VERDICT_PAGES_WITHOUT_INSTAGRAM
    assert diagnostics["permissions"]["granted"] == ["instagram_basic", "pages_show_list"]
    assert diagnostics["permissions"]["declined"] == ["read_insights"]
    assert diagnostics["token"]["granular_scopes"] == {"pages_show_list": ["page-1"]}
    assert diagnostics["pages"]["count"] == 1
    assert diagnostics["pages"]["items"][0]["instagram_business_account"] == ""
    assert diagnostics["errors"] == []
    assert page_names(diagnostics) == ["Orbita Media Verlag"]


def test_diagnostics_take_the_same_fallback_as_the_connect():
    """Sonst meldet die Diagnose "keine Seite", während das Verbinden längst welche hat.

    Noahs Fall: ``/me/accounts`` schweigt, die Kennungen stehen im Token. Die
    Diagnose muss dieselben Seiten sehen, sonst schickt sie den Nutzer mit einer
    falschen Begründung los.
    """
    request_fn = _request_stub(
        {
            "/me/permissions": {"data": [{"permission": "pages_show_list", "status": "granted"}]},
            "/debug_token": {
                "data": {
                    "app_id": "app",
                    "is_valid": True,
                    "granular_scopes": [
                        {"scope": "instagram_basic", "target_ids": ["17841466348000992"]},
                        {"scope": "pages_show_list", "target_ids": ["254978271039996"]},
                        {"scope": "pages_read_engagement", "target_ids": ["254978271039996"]},
                    ],
                }
            },
            "/me/accounts": {"data": []},
            "/254978271039996": {
                "id": "254978271039996",
                "name": "Orbita Media Verlag",
                "category": "Publisher",
                "tasks": ["MANAGE", "CREATE_CONTENT"],
            },
        }
    )

    diagnostics = collect_diagnostics(
        request_fn,
        base_url="https://graph.facebook.com/v25.0",
        access_token="user-token",
        app_id="app",
        app_secret="secret",
    )

    assert diagnostics["pages"]["source"] == "granular_scopes"
    assert diagnostics["pages"]["count"] == 1
    assert page_names(diagnostics) == ["Orbita Media Verlag"]
    # Die Seite ist da, nur die Verknüpfung fehlt – das ist ein anderer Rat als
    # "Sie haben gar keine Seite".
    assert diagnostics["verdict"] == VERDICT_PAGES_WITHOUT_INSTAGRAM
    assert pages_without_content_role(diagnostics) == []


def test_diagnostics_report_which_way_found_the_pages():
    """Kommen die Seiten über die Sammelabfrage, steht auch das im Befund."""
    request_fn = _request_stub(
        {
            "/me/permissions": {"data": []},
            "/me/accounts": {"data": [{"id": "page-1", "name": "Seite", "tasks": ["MANAGE"]}]},
        }
    )

    diagnostics = collect_diagnostics(
        request_fn,
        base_url="https://graph.facebook.com/v25.0",
        access_token="user-token",
    )

    assert diagnostics["pages"]["source"] == "me_accounts"


def test_collect_diagnostics_never_leaks_the_access_token():
    request_fn = _request_stub(
        {
            "/me/permissions": {"data": []},
            "/debug_token": {"data": {"app_id": "app", "scopes": []}},
            "/me/accounts": {"data": []},
        }
    )

    diagnostics = collect_diagnostics(
        request_fn,
        base_url="https://graph.facebook.com/v25.0",
        access_token="EAAsupersecrettoken",
        app_id="app",
        app_secret="secret",
    )

    assert "EAAsupersecrettoken" not in repr(diagnostics)
    assert "secret" not in repr(diagnostics.get("token", {}))
    assert diagnostics["verdict"] == VERDICT_NO_PAGES


def test_collect_diagnostics_falls_back_when_the_field_set_is_rejected():
    calls: list[str] = []

    def _call(method, url, **kwargs):
        if "/me/permissions" in url:
            return _resp({"data": []})
        if "/me/accounts" in url:
            fields = kwargs["params"]["fields"]
            calls.append(fields)
            if "connected_instagram_account" in fields:
                raise APIError("Instagram API error 400: nonexisting field", status_code=400)
            return _resp({"data": [{"id": "page-1", "name": "Seite", "instagram_business_account": {"id": "ig-1"}}]})
        raise AssertionError(url)

    diagnostics = collect_diagnostics(
        MagicMock(side_effect=_call),
        base_url="https://graph.facebook.com/v25.0",
        access_token="user-token",
    )

    assert len(calls) == 2, "die ausführlichere Feldliste muss zuerst versucht werden"
    assert diagnostics["pages"]["items"][0]["instagram_business_account"] == "ig-1"
    assert diagnostics["verdict"] == VERDICT_PAGES_WITH_INSTAGRAM


def test_collect_diagnostics_records_a_failing_step_instead_of_raising():
    request_fn = _request_stub(
        {
            "/me/permissions": APIError(
                "Instagram API error 190: token expired",
                status_code=190,
                raw_response={"error": {"code": 190, "type": "OAuthException", "message": "token expired"}},
            ),
            "/me/accounts": {"data": []},
        }
    )

    diagnostics = collect_diagnostics(
        request_fn,
        base_url="https://graph.facebook.com/v25.0",
        access_token="user-token",
    )

    assert diagnostics["errors"][0]["step"] == "permissions"
    assert diagnostics["errors"][0]["error"]["code"] == 190
    assert diagnostics["errors"][0]["error"]["type"] == "OAuthException"
    assert diagnostics["verdict"] == VERDICT_NO_PAGES


def test_classify_without_page_data_stays_unknown():
    assert classify({}) == "unknown"
    assert classify({"pages": {"count": 0, "items": []}}) == VERDICT_NO_PAGES


def test_redact_removes_anything_shaped_like_a_token():
    text = "error for token EAAG9ZBxyzAbCdEf1234567890 with app 1062167552935661|abcdef0123456789abcdef"

    cleaned = redact(text)

    assert "EAAG9ZBxyzAbCdEf1234567890" not in cleaned
    assert "1062167552935661|abcdef0123456789abcdef" not in cleaned
    assert cleaned.count("[entfernt]") == 2


def test_a_failing_step_reports_a_redacted_message():
    """Der Fehlertext eines Providers trägt den Anfang der Antwort mit sich."""
    request_fn = _request_stub(
        {
            "/me/permissions": APIError(
                'Instagram API error 400: {"access_token":"EAAG9ZBleakedtokenvalue1234"}',
                status_code=400,
            ),
            "/me/accounts": {"data": []},
        }
    )

    diagnostics = collect_diagnostics(
        request_fn,
        base_url="https://graph.facebook.com/v25.0",
        access_token="user-token",
    )

    assert "EAAG9ZBleakedtokenvalue1234" not in repr(diagnostics)
    assert "[entfernt]" in diagnostics["errors"][0]["error"]["message"]


def test_pages_without_content_role_separates_missing_rights_from_missing_link():
    diagnostics = {
        "pages": {
            "fields": "id,name,category,tasks,instagram_business_account{id,username}",
            "items": [
                {"id": "page-1", "name": "Nur Werbung", "tasks": ["ADVERTISE", "ANALYZE"]},
                {"id": "page-2", "name": "Volle Rechte", "tasks": ["MANAGE", "CREATE_CONTENT"]},
            ],
        }
    }

    assert pages_without_content_role(diagnostics) == ["Nur Werbung"]


def test_pages_without_content_role_says_nothing_when_tasks_were_not_asked_for():
    """Ohne abgefragte tasks heisst leer "nicht erhoben", nicht "keine Rechte"."""
    diagnostics = {"pages": {"fields": "id,name", "items": [{"id": "page-1", "name": "Seite"}]}}

    assert pages_without_content_role(diagnostics) == []


def test_instagram_links_lists_only_pages_that_carry_an_account():
    diagnostics = {
        "pages": {
            "items": [
                {"id": "page-1", "name": "Ohne Konto", "instagram_business_account": ""},
                {"id": "page-2", "name": "Mit Konto", "connected_instagram_account": "17841466348000992"},
            ]
        }
    }

    assert instagram_links(diagnostics) == [("Mit Konto", "17841466348000992")]


def test_provider_diagnose_pages_uses_the_app_credentials():
    provider = InstagramProvider({"app_id": "app-1", "app_secret": "secret-1"})
    provider._request = _request_stub(
        {
            "/me/permissions": {"data": [{"permission": "pages_show_list", "status": "granted"}]},
            "/debug_token": {"data": {"app_id": "app-1", "scopes": ["pages_show_list"]}},
            "/me/accounts": {"data": []},
        }
    )

    diagnostics = provider.diagnose_pages("user-token")

    assert diagnostics["token"]["app_id"] == "app-1"
    assert diagnostics["verdict"] == VERDICT_NO_PAGES
