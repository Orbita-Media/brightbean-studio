"""Google Business Profile provider against the recorded shape of the real API.

Anlass (25.09.2026): The API quota was 0 until that day, so the provider had
never run against Google. A comparison with the reference found three bugs that
would have failed before the first post, and two gaps:

* F1 ``accounts.locations.list`` without the *required* ``readMask`` → 400
  already while connecting (``get_profile`` runs in the OAuth callback),
* F2 posts went to ``v4/locations/{id}/localPosts`` – the v4 parent is
  ``accounts/{a}/locations/{l}`` → 404,
* F3 metrics read ``searchActionMetrics`` off the post, which has none → 0,
* L1 ``link_url`` was dropped, so no "Kaufen" button,
* L2 ``languageCode`` fell back to ``"en"``.

Shapes: developers.google.com/my-business/reference/rest (v4 localPosts,
v1 accounts.locations.list, discovery document mybusiness_google_rest_v4p9).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from providers.exceptions import PublishError
from providers.google_business import (
    ACCOUNTS_API,
    BUSINESS_INFO_API,
    CTA_TYPES,
    POSTS_API,
    GoogleBusinessProvider,
    location_number,
)
from providers.types import PostType, PublishContent

KONTO = "accounts/113894727651234567890"
PERSOENLICH = "accounts/100000000000000000001"
STANDORT = "locations/2832287425028066333"
PARENT = f"{KONTO}/locations/2832287425028066333"
BUCHSEITE = "https://orbita-media.de/buecher/festland?utm_source=google&utm_medium=social&utm_campaign=gbp-2026-10-15"

ORBITA = {
    "name": STANDORT,
    "title": "Orbita Media GmbH",
    "storefrontAddress": {"addressLines": ["Ericusspitze 4"]},
    "phoneNumbers": {"primaryPhone": "040 1234567"},
}


def _antwort(daten: dict) -> MagicMock:
    return MagicMock(json=MagicMock(return_value=daten))


def _provider(*, credentials: dict | None = None, antworten: dict | None = None) -> GoogleBusinessProvider:
    """Provider whose ``_request`` answers like Google.

    ``antworten`` maps ``(method, url)`` to a JSON body or a list of bodies
    (one per call, for paging).
    """
    provider = GoogleBusinessProvider(credentials={"client_id": "id", "client_secret": "geheim", **(credentials or {})})
    provider.aufrufe = []
    tabelle = {
        ("GET", f"{ACCOUNTS_API}/accounts"): {"accounts": [{"name": PERSOENLICH}, {"name": KONTO}]},
        ("GET", f"{BUSINESS_INFO_API}/{PERSOENLICH}/locations"): {},
        ("GET", f"{BUSINESS_INFO_API}/{KONTO}/locations"): {"locations": [ORBITA]},
        ("POST", f"{POSTS_API}/{PARENT}/localPosts"): {
            "name": f"{PARENT}/localPosts/987",
            "state": "LIVE",
            "searchUrl": "https://local.google.com/place?id=1&use=posts&lpsid=987",
        },
        **(antworten or {}),
    }

    def fake_request(method, url, **kwargs):
        provider.aufrufe.append((method, url, kwargs))
        wert = tabelle[(method, url)]
        if isinstance(wert, list):
            wert = wert.pop(0)
        return _antwort(wert)

    provider._request = MagicMock(side_effect=fake_request)
    return provider


def _content(**kwargs) -> PublishContent:
    return PublishContent(
        text="Sieben Tage, vier Menschen, eine Insel.",
        post_type=PostType.IMAGE,
        media_urls=["https://social.orbita-media.de/media/festland.jpg"],
        **kwargs,
    )


class TestConnect:
    def test_location_list_sends_the_required_read_mask(self):
        provider = _provider()
        provider.get_profile("t")
        listen = [k for m, u, k in provider.aufrufe if u.endswith("/locations")]
        assert listen, "locations.list was not called"
        for kwargs in listen:
            assert kwargs["params"]["readMask"] == "name,title,storefrontAddress,phoneNumbers"

    def test_profile_is_the_location_under_the_account_that_owns_it(self):
        # The first account is the personal one without locations.
        profil = _provider().get_profile("t")
        assert profil.platform_id == PARENT
        assert profil.name == "Orbita Media GmbH"
        assert profil.extra["address"] == "Ericusspitze 4"
        assert profil.extra["account"] == KONTO

    def test_all_pages_are_read(self):
        provider = _provider(
            antworten={
                ("GET", f"{ACCOUNTS_API}/accounts"): [
                    {"accounts": [{"name": PERSOENLICH}], "nextPageToken": "s2"},
                    {"accounts": [{"name": KONTO}]},
                ],
            }
        )
        assert provider.get_profile("t").platform_id == PARENT
        seiten = [k["params"].get("pageToken") for m, u, k in provider.aufrufe if u.endswith("/accounts")]
        assert seiten == [None, "s2"]

    def test_configured_location_id_picks_that_location(self):
        andere = {**ORBITA, "name": "locations/1", "title": "Andere"}
        provider = _provider(
            credentials={"location_id": "2832287425028066333"},
            antworten={("GET", f"{BUSINESS_INFO_API}/{KONTO}/locations"): {"locations": [andere, ORBITA]}},
        )
        assert provider.get_profile("t").name == "Orbita Media GmbH"

    def test_no_location_anywhere_is_a_clear_error(self):
        provider = _provider(antworten={("GET", f"{BUSINESS_INFO_API}/{KONTO}/locations"): {}})
        with pytest.raises(PublishError, match="No Google Business location"):
            provider.get_profile("t")


class TestPublish:
    def test_post_goes_to_the_v4_parent_from_the_connected_account(self):
        provider = _provider()
        ergebnis = provider.publish_post("t", _content(extra={"location_path": PARENT}))
        methode, url, _ = provider.aufrufe[-1]
        assert (methode, url) == ("POST", f"{POSTS_API}/{PARENT}/localPosts")
        # No lookup needed when the engine hands over the path.
        assert len(provider.aufrufe) == 1
        assert ergebnis.platform_post_id == f"{PARENT}/localPosts/987"

    def test_old_v1_location_name_is_never_used_as_v4_parent(self):
        provider = _provider()
        provider.publish_post("t", _content(extra={"location_path": STANDORT}))
        assert provider.aufrufe[-1][1] == f"{POSTS_API}/{PARENT}/localPosts"

    @pytest.mark.parametrize("location_id", ["2832287425028066333", STANDORT, PARENT])
    def test_credentials_accept_every_spelling_of_the_location(self, location_id):
        provider = _provider(credentials={"account_id": "113894727651234567890", "location_id": location_id})
        provider.publish_post("t", _content())
        assert provider.aufrufe[-1][1] == f"{POSTS_API}/{PARENT}/localPosts"

    def test_shop_button_with_book_page_and_german_language(self):
        provider = _provider()
        provider.publish_post("t", _content(link_url=BUCHSEITE, extra={"location_path": PARENT, "gbp_cta": "SHOP"}))
        body = provider.aufrufe[-1][2]["json"]
        assert body["callToAction"] == {"actionType": "SHOP", "url": BUCHSEITE}
        assert body["languageCode"] == "de"
        assert body["topicType"] == "STANDARD"
        assert body["summary"].startswith("Sieben Tage")
        assert body["media"] == [
            {"mediaFormat": "PHOTO", "sourceUrl": "https://social.orbita-media.de/media/festland.jpg"}
        ]

    def test_link_without_button_type_gets_learn_more(self):
        body = _provider().build_post_body(_content(link_url=BUCHSEITE))
        assert body["callToAction"] == {"actionType": "LEARN_MORE", "url": BUCHSEITE}

    def test_no_link_no_button(self):
        assert "callToAction" not in _provider().build_post_body(_content())

    def test_call_button_carries_no_url(self):
        body = _provider().build_post_body(_content(link_url=BUCHSEITE, extra={"gbp_cta": "CALL"}))
        assert body["callToAction"] == {"actionType": "CALL"}

    def test_button_without_link_is_refused(self):
        with pytest.raises(PublishError, match="needs a link"):
            _provider().build_post_body(_content(extra={"gbp_cta": "SHOP"}))

    def test_unknown_button_is_refused(self):
        with pytest.raises(PublishError, match="Unknown Google Business button"):
            _provider().build_post_body(_content(link_url=BUCHSEITE, extra={"gbp_cta": "BUY"}))

    def test_button_types_are_the_v4_enum_without_the_deprecated_get_offer(self):
        assert {"BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"} == CTA_TYPES

    def test_text_over_1500_characters_is_refused_before_any_call(self):
        provider = _provider()
        with pytest.raises(PublishError, match="1500"):
            provider.publish_post("t", PublishContent(text="a" * 1501, extra={"location_path": PARENT}))
        assert provider.aufrufe == []

    def test_rejected_post_is_an_error_not_a_success(self):
        provider = _provider(
            antworten={
                ("POST", f"{POSTS_API}/{PARENT}/localPosts"): {"name": f"{PARENT}/localPosts/1", "state": "REJECTED"}
            }
        )
        with pytest.raises(PublishError, match="REJECTED"):
            provider.publish_post("t", _content(extra={"location_path": PARENT}))

    def test_only_one_image_is_sent(self):
        inhalt = _content(extra={"location_path": PARENT})
        inhalt.media_urls.append("https://social.orbita-media.de/media/zwei.jpg")
        assert len(_provider().build_post_body(inhalt)["media"]) == 1


class TestMetrics:
    def test_metrics_come_from_report_insights(self):
        post = f"{PARENT}/localPosts/987"
        provider = _provider(
            antworten={
                ("POST", f"{POSTS_API}/{PARENT}/localPosts:reportInsights"): {
                    "name": PARENT,
                    "localPostMetrics": [
                        {
                            "localPostName": post,
                            "metricValues": [
                                {"metric": "LOCAL_POST_VIEWS_SEARCH", "totalValue": {"value": "412"}},
                                {"metric": "LOCAL_POST_ACTIONS_CALL_TO_ACTION", "totalValue": {"value": "17"}},
                            ],
                        }
                    ],
                }
            }
        )
        metriken = provider.get_post_metrics("t", post)
        assert metriken.impressions == 412
        assert metriken.clicks == 17
        body = provider.aufrufe[-1][2]["json"]
        assert body["localPostNames"] == [post]
        assert body["basicRequest"]["metricRequests"] == [{"metric": "ALL"}]
        assert body["basicRequest"]["timeRange"]["endTime"].endswith("Z")

    def test_a_non_local_post_name_is_refused(self):
        with pytest.raises(PublishError):
            _provider().get_post_metrics("t", "987")


def test_location_number_reads_every_spelling():
    for wert in ("2832287425028066333", STANDORT, PARENT, f"{PARENT}/"):
        assert location_number(wert) == "2832287425028066333"
