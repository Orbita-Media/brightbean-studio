from datetime import UTC, datetime
from unittest.mock import MagicMock, call

from providers.exceptions import APIError
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def test_get_user_pages_returns_linked_instagram_business_accounts():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "data": [
                        {
                            "id": "page-1",
                            "name": "Facebook Page",
                            "access_token": "page-token",
                            "category": "Creator",
                            "picture": {"data": {"url": "https://example.com/page.jpg"}},
                            "instagram_business_account": {
                                "id": "17841400000000000",
                                "username": "brightbean",
                                "name": "Brightbean",
                                "profile_picture_url": "https://example.com/ig.jpg",
                                "followers_count": 42,
                            },
                        },
                        {
                            "id": "page-2",
                            "name": "No Instagram Here",
                            "access_token": "unused-token",
                        },
                    ]
                }
            )
        )
    )

    accounts = provider.get_user_pages("user-token")

    assert accounts == [
        {
            "id": "17841400000000000",
            "name": "Brightbean",
            "handle": "brightbean",
            "access_token": "page-token",
            "category": "Creator",
            "picture": "https://example.com/ig.jpg",
            "followers_count": 42,
            "page_id": "page-1",
            "page_name": "Facebook Page",
        }
    ]
    provider._request.assert_called_once_with(
        "GET",
        "https://graph.facebook.com/v25.0/me/accounts",
        access_token="user-token",
        params={
            "fields": (
                "id,name,access_token,category,picture,"
                "instagram_business_account{id,username,name,profile_picture_url,followers_count,media_count}"
            ),
        },
    )


def test_get_user_pages_omits_blank_page_access_token():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})

    provider._request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "data": [
                        {
                            "id": "page-1",
                            "name": "Facebook Page",
                            "access_token": "",
                            "instagram_business_account": {
                                "id": "17841400000000000",
                                "username": "brightbean",
                                "name": "Brightbean",
                            },
                        },
                    ]
                }
            )
        )
    )

    accounts = provider.get_user_pages("user-token")

    assert len(accounts) == 1
    assert "access_token" not in accounts[0]


def test_account_metrics_use_current_instagram_insights_metrics():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret", "ig_user_id": "ig-1"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"data": [{"name": "reach", "values": [{"value": 12}]}]}),
            _resp({"data": [{"name": "views", "period": "day", "total_value": {"value": 67}}]}),
            _resp({"data": [{"name": "accounts_engaged", "values": [{"value": 8}]}]}),
            _resp({"data": [{"name": "total_interactions", "values": [{"value": 9}]}]}),
            _resp({"followers_count": 34}),
        ]
    )

    metrics = provider.get_account_metrics(
        "page-token",
        (
            datetime(2026, 6, 18, tzinfo=UTC),
            datetime(2026, 6, 19, tzinfo=UTC),
        ),
    )

    assert metrics.impressions == 0
    assert metrics.reach == 12
    assert metrics.followers == 34
    assert metrics.extra["views"] == 67
    provider._request.assert_has_calls(
        [
            call(
                "GET",
                "https://graph.facebook.com/v25.0/ig-1/insights",
                access_token="page-token",
                params={
                    "metric": "reach",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
            call(
                "GET",
                "https://graph.facebook.com/v25.0/ig-1/insights",
                access_token="page-token",
                params={
                    "metric": "views",
                    "period": "day",
                    "metric_type": "total_value",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
            call(
                "GET",
                "https://graph.facebook.com/v25.0/ig-1/insights",
                access_token="page-token",
                params={
                    "metric": "accounts_engaged",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                    "metric_type": "total_value",
                },
            ),
            call(
                "GET",
                "https://graph.facebook.com/v25.0/ig-1/insights",
                access_token="page-token",
                params={
                    "metric": "total_interactions",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                    "metric_type": "total_value",
                },
            ),
            call(
                "GET",
                "https://graph.facebook.com/v25.0/ig-1",
                access_token="page-token",
                params={"fields": "id,username,name,profile_picture_url,followers_count,media_count"},
            ),
        ]
    )


def test_instagram_media_metrics_use_current_metrics_and_field_fallbacks():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "ig-media-1", "like_count": 12, "comments_count": 3}),
            _resp({"data": [{"name": "reach", "values": [{"value": 250}]}]}),
            _resp({"data": [{"name": "views", "values": [{"value": 400}]}]}),
            _resp({"data": [{"name": "likes", "values": [{"value": 12}]}]}),
            _resp({"data": [{"name": "comments", "values": [{"value": 3}]}]}),
            _resp({"data": [{"name": "saved", "values": [{"value": 5}]}]}),
            _resp({"data": [{"name": "shares", "values": [{"value": 2}]}]}),
            _resp({"data": [{"name": "total_interactions", "values": [{"value": 22}]}]}),
        ]
    )

    metrics = provider.get_post_metrics("page-token", "ig-media-1")

    assert metrics.video_views == 400
    assert metrics.reach == 250
    assert metrics.likes == 12
    assert metrics.comments == 3
    assert metrics.saves == 5
    assert metrics.shares == 2
    assert metrics.extra["total_interactions"] == 22


def test_instagram_login_account_metrics_use_current_insights_metrics():
    provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"data": [{"name": "reach", "values": [{"value": 12}]}]}),
            _resp({"data": [{"name": "views", "period": "day", "total_value": {"value": 67}}]}),
            _resp({"data": [{"name": "accounts_engaged", "values": [{"value": 8}]}]}),
            _resp({"data": [{"name": "total_interactions", "values": [{"value": 9}]}]}),
            _resp({"followers_count": 34}),
        ]
    )

    metrics = provider.get_account_metrics(
        "ig-token",
        (
            datetime(2026, 6, 18, tzinfo=UTC),
            datetime(2026, 6, 19, tzinfo=UTC),
        ),
    )

    assert metrics.impressions == 0
    assert metrics.reach == 12
    assert metrics.followers == 34
    assert metrics.extra["views"] == 67
    provider._request.assert_has_calls(
        [
            call(
                "GET",
                "https://graph.instagram.com/v25.0/me/insights",
                access_token="ig-token",
                params={
                    "metric": "reach",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
            call(
                "GET",
                "https://graph.instagram.com/v25.0/me/insights",
                access_token="ig-token",
                params={
                    "metric": "views",
                    "period": "day",
                    "metric_type": "total_value",
                    "since": 1781740800,
                    "until": 1781827200,
                },
            ),
            call(
                "GET",
                "https://graph.instagram.com/v25.0/me/insights",
                access_token="ig-token",
                params={
                    "metric": "accounts_engaged",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                    "metric_type": "total_value",
                },
            ),
            call(
                "GET",
                "https://graph.instagram.com/v25.0/me/insights",
                access_token="ig-token",
                params={
                    "metric": "total_interactions",
                    "period": "day",
                    "since": 1781740800,
                    "until": 1781827200,
                    "metric_type": "total_value",
                },
            ),
            call(
                "GET",
                "https://graph.instagram.com/v25.0/me",
                access_token="ig-token",
                params={"fields": "user_id,username,name,profile_picture_url,followers_count,media_count"},
            ),
        ]
    )


def test_account_metrics_followers_none_when_profile_fetch_fails():
    """A transient profile-fetch failure must yield followers=None (not 0) so the
    analytics layer can skip it instead of writing a poisoning 0 snapshot for a
    real account."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret", "ig_user_id": "ig-1"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"data": [{"name": "reach", "values": [{"value": 12}]}]}),
            _resp({"data": [{"name": "views", "period": "day", "total_value": {"value": 67}}]}),
            _resp({"data": [{"name": "accounts_engaged", "values": [{"value": 8}]}]}),
            _resp({"data": [{"name": "total_interactions", "values": [{"value": 9}]}]}),
            APIError("(#190) Error validating access token", platform="Instagram"),
        ]
    )

    metrics = provider.get_account_metrics(
        "page-token",
        (datetime(2026, 6, 18, tzinfo=UTC), datetime(2026, 6, 19, tzinfo=UTC)),
    )

    assert metrics.followers is None
    assert metrics.reach == 12


# ---------------------------------------------------------------------------
# Ausweichweg über connected_instagram_account
# ---------------------------------------------------------------------------


def _page_without_business_account():
    return {
        "data": [
            {
                "id": "page-1",
                "name": "Orbita Media Verlag",
                "access_token": "page-token",
                "category": "Publisher",
                "picture": {"data": {"url": "https://example.com/page.jpg"}},
            }
        ]
    }


def test_get_user_pages_falls_back_to_connected_instagram_account():
    """Trägt die Seite die Verknüpfung nur im zweiten Feld, zählt sie trotzdem."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp(_page_without_business_account()),
            _resp(
                {
                    "data": [
                        {
                            "id": "page-1",
                            "name": "Orbita Media Verlag",
                            "access_token": "page-token",
                            "category": "Publisher",
                            "picture": {"data": {"url": "https://example.com/page.jpg"}},
                            "connected_instagram_account": {
                                "id": "17841466348000992",
                                "username": "orbitamedia_verlag",
                            },
                        }
                    ]
                }
            ),
            _resp(
                {
                    "id": "17841466348000992",
                    "username": "orbitamedia_verlag",
                    "name": "Orbita Media Verlag",
                    "profile_picture_url": "https://example.com/ig.jpg",
                    "followers_count": 7,
                }
            ),
        ]
    )

    accounts = provider.get_user_pages("user-token")

    assert accounts == [
        {
            "id": "17841466348000992",
            "name": "Orbita Media Verlag",
            "handle": "orbitamedia_verlag",
            "category": "Publisher",
            "picture": "https://example.com/ig.jpg",
            "followers_count": 7,
            "page_id": "page-1",
            "page_name": "Orbita Media Verlag",
            "link_source": "connected_instagram_account",
            "access_token": "page-token",
        }
    ]


def test_connected_instagram_account_is_skipped_when_it_is_not_professional():
    """Ein privates Konto beantwortet keine Profilfelder und wird nicht angeboten."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp(_page_without_business_account()),
            _resp(
                {
                    "data": [
                        {
                            "id": "page-1",
                            "name": "Orbita Media Verlag",
                            "connected_instagram_account": {"id": "17841400000000001"},
                        }
                    ]
                }
            ),
            APIError("Instagram API error 100: unsupported get request", platform="Instagram"),
        ]
    )

    assert provider.get_user_pages("user-token") == []


def test_connected_instagram_account_failure_does_not_break_the_connect():
    """Fällt das undokumentierte Feld aus, bleibt es bei der leeren Liste."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp(_page_without_business_account()),
            APIError("Instagram API error 400: nonexisting field", platform="Instagram"),
        ]
    )

    assert provider.get_user_pages("user-token") == []


def test_no_page_anywhere_ends_the_search():
    """Nennt das Token keine Seite, folgt der Blick in die Portfolios – dann ist Schluss.

    Die ``instagram_basic``-Kennung ist bewusst dabei: Sie ist die Kennung des
    Kontos, nicht die einer Seite, und darf keine Seitenabfrage auslösen.
    """
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"data": []}),
            _resp({"data": {"granular_scopes": [{"scope": "instagram_basic", "target_ids": ["ig-1"]}]}}),
            _resp({"data": []}),
        ]
    )

    assert provider.get_user_pages("user-token") == []
    assert provider._request.call_count == 3, "Seitenabfrage, Token-Abfrage, Portfolio-Abfrage"
    assert provider._request.call_args_list[-1][0][1].endswith("/me/businesses")


# ---------------------------------------------------------------------------
# Ausweichweg: Seiten aus einem Business-Portfolio
# ---------------------------------------------------------------------------
#
# Noahs Fall vom 06.08.2026, mit den echten Kennungen aus dem Protokoll:
# /me/accounts liefert null Seiten, obwohl das Token beide Seiten und das
# Instagram-Konto nennt. Die Seiten gehören einem Business-Portfolio.

PORTFOLIO_PAGE_A = "708768612318133"
PORTFOLIO_PAGE_B = "254978271039996"
PORTFOLIO_IG = "17841466348000992"


def _portfolio_token_payload():
    return {
        "data": {
            "app_id": "1062167552935661",
            "user_id": "1069610232678392",
            "type": "USER",
            "is_valid": True,
            "granular_scopes": [
                {"scope": "instagram_basic", "target_ids": [PORTFOLIO_IG]},
                {"scope": "instagram_content_publish", "target_ids": [PORTFOLIO_IG]},
                {"scope": "pages_read_engagement", "target_ids": [PORTFOLIO_PAGE_A, PORTFOLIO_PAGE_B]},
                {"scope": "pages_show_list", "target_ids": [PORTFOLIO_PAGE_A, PORTFOLIO_PAGE_B]},
            ],
        }
    }


def _portfolio_graph(page_b_payload, page_a_payload=None):
    def _call(method, url, **kwargs):
        if url.endswith("/me/accounts"):
            return _resp({"data": []})
        if url.endswith("/debug_token"):
            return _resp(_portfolio_token_payload())
        if url.endswith(f"/{PORTFOLIO_PAGE_A}"):
            return _resp(page_a_payload or {"id": PORTFOLIO_PAGE_A, "name": "Orbita Media"})
        if url.endswith(f"/{PORTFOLIO_PAGE_B}"):
            return _resp(page_b_payload)
        raise AssertionError(f"Unerwarteter Aufruf: {url}")

    return MagicMock(side_effect=_call)


def test_portfolio_pages_are_found_through_the_ids_in_the_token():
    """Der Fix: leere Sammelabfrage, Kennungen aus dem Token, Seite einzeln geholt."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = _portfolio_graph(
        {
            "id": PORTFOLIO_PAGE_B,
            "name": "Orbita Media Verlag",
            "access_token": "seiten-schlüssel",
            "category": "Publisher",
            "picture": {"data": {"url": "https://example.com/page.jpg"}},
            "instagram_business_account": {
                "id": PORTFOLIO_IG,
                "username": "orbitamedia_verlag",
                "name": "Orbita Media Verlag",
                "profile_picture_url": "https://example.com/ig.jpg",
                "followers_count": 7,
            },
        }
    )

    accounts = provider.get_user_pages("user-token")

    assert accounts == [
        {
            "id": PORTFOLIO_IG,
            "name": "Orbita Media Verlag",
            "handle": "orbitamedia_verlag",
            "category": "Publisher",
            "picture": "https://example.com/ig.jpg",
            "followers_count": 7,
            "page_id": PORTFOLIO_PAGE_B,
            "page_name": "Orbita Media Verlag",
            "access_token": "seiten-schlüssel",
        }
    ]


def test_portfolio_pages_also_work_through_the_second_link_field():
    """Trägt die Seite die Verknüpfung nur im zweiten Feld, zählt sie auch hier."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})

    def _call(method, url, **kwargs):
        if url.endswith("/me/accounts"):
            return _resp({"data": []})
        if url.endswith("/debug_token"):
            return _resp(_portfolio_token_payload())
        if url.endswith(f"/{PORTFOLIO_PAGE_A}"):
            return _resp({"id": PORTFOLIO_PAGE_A, "name": "Orbita Media"})
        if url.endswith(f"/{PORTFOLIO_PAGE_B}"):
            return _resp(
                {
                    "id": PORTFOLIO_PAGE_B,
                    "name": "Orbita Media Verlag",
                    "access_token": "seiten-schlüssel",
                    "category": "Publisher",
                    "connected_instagram_account": {"id": PORTFOLIO_IG, "username": "orbitamedia_verlag"},
                }
            )
        if url.endswith(f"/{PORTFOLIO_IG}"):
            return _resp(
                {
                    "id": PORTFOLIO_IG,
                    "username": "orbitamedia_verlag",
                    "name": "Orbita Media Verlag",
                    "followers_count": 7,
                }
            )
        raise AssertionError(f"Unerwarteter Aufruf: {url}")

    provider._request = MagicMock(side_effect=_call)

    accounts = provider.get_user_pages("user-token")

    assert len(accounts) == 1
    assert accounts[0]["id"] == PORTFOLIO_IG
    assert accounts[0]["link_source"] == "connected_instagram_account"
    assert accounts[0]["access_token"] == "seiten-schlüssel"
    # Der Ausweichweg hat beide Verknüpfungsfelder schon verlangt: ein zweites
    # /me/accounts wäre verschenkt und liefe ohnehin wieder ins Leere.
    accounts_calls = [c for c in provider._request.call_args_list if c.args[1].endswith("/me/accounts")]
    assert len(accounts_calls) == 1


def test_the_log_says_which_way_found_the_pages(caplog):
    """Beim nächsten Konto soll sofort sichtbar sein, welcher Weg gegriffen hat."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = _portfolio_graph(
        {
            "id": PORTFOLIO_PAGE_B,
            "name": "Orbita Media Verlag",
            "access_token": "seiten-schlüssel",
            "instagram_business_account": {"id": PORTFOLIO_IG, "username": "orbitamedia_verlag"},
        }
    )

    with caplog.at_level("INFO"):
        provider.get_user_pages("user-token")

    assert "granular_scopes" in caplog.text
    assert "seiten-schlüssel" not in caplog.text


def test_the_page_access_token_is_requested_separately_when_it_is_missing():
    """Kommt der Seitenschlüssel nicht mit, wird er einzeln nachgefordert."""
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    asked_for_token = []

    def _call(method, url, **kwargs):
        if url.endswith("/me/accounts"):
            return _resp({"data": []})
        if url.endswith("/debug_token"):
            return _resp(_portfolio_token_payload())
        if url.endswith(f"/{PORTFOLIO_PAGE_A}"):
            return _resp({"id": PORTFOLIO_PAGE_A, "name": "Orbita Media"})
        if url.endswith(f"/{PORTFOLIO_PAGE_B}"):
            if kwargs["params"]["fields"] == "access_token":
                asked_for_token.append(url)
                return _resp({"id": PORTFOLIO_PAGE_B, "access_token": "nachgereicht"})
            return _resp(
                {
                    "id": PORTFOLIO_PAGE_B,
                    "name": "Orbita Media Verlag",
                    "instagram_business_account": {"id": PORTFOLIO_IG, "username": "orbitamedia_verlag"},
                }
            )
        raise AssertionError(f"Unerwarteter Aufruf: {url}")

    provider._request = MagicMock(side_effect=_call)

    accounts = provider.get_user_pages("user-token")

    assert asked_for_token, "der Seitenschlüssel muss einzeln nachgefordert werden"
    assert accounts[0]["access_token"] == "nachgereicht"
