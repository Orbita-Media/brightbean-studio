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
    page_names,
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
