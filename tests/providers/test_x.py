"""Tests für den Orbita-eigenen X-Provider (providers/x.py).

Diesen Provider gibt es im Upstream nicht. Die Tests sichern vor allem den
PKCE-Vertrag ab, auf den der Provider beim Upstream-Merge am 24.07.2026
umgestellt wurde (vorher: Verifier deterministisch aus dem state, Methode
plain; jetzt: Verifier aus der OAuth-Session, code_challenge nach RFC 7636
mit S256). Wird der Provider bei einem künftigen Merge versehentlich auf
den alten Sonderweg zurückgesetzt, schlagen diese Tests fehl.
"""

import base64
import hashlib
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from providers.exceptions import OAuthError, PublishError
from providers.types import PublishContent
from providers.x import XProvider

CREDS = {"client_id": "cid", "client_secret": "csecret"}
# RFC 7636 verlangt 43-128 Zeichen aus dem unreserved-Alphabet.
VERIFIER = "test-verifier-0123456789-0123456789-0123456789"


def _make_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


def _expected_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class TestPkceContract:
    def test_provider_declares_pkce(self):
        assert XProvider(CREDS).uses_pkce is True

    def test_auth_url_uses_s256_challenge_from_verifier(self):
        url = XProvider(CREDS).get_auth_url("https://app.example/cb", "state-123", code_verifier=VERIFIER)

        query = parse_qs(urlsplit(url).query)
        assert query["code_challenge"] == [_expected_challenge(VERIFIER)]
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"] == ["state-123"]
        # Regression: der Challenge darf NICHT mehr der Verifier selbst sein
        # (das war der alte plain-Weg).
        assert query["code_challenge"] != [VERIFIER]

    def test_auth_url_challenge_is_base64url_not_hex(self):
        """Abgrenzung zu TikTok, das den HEX-Digest erwartet."""
        url = XProvider(CREDS).get_auth_url("https://app.example/cb", "state-123", code_verifier=VERIFIER)

        challenge = parse_qs(urlsplit(url).query)["code_challenge"][0]
        assert len(challenge) == 43  # base64url ohne Padding
        assert challenge != hashlib.sha256(VERIFIER.encode()).hexdigest()

    def test_auth_url_requires_verifier(self):
        with pytest.raises(OAuthError):
            XProvider(CREDS).get_auth_url("https://app.example/cb", "state-123")

    def test_auth_url_is_not_derived_from_state(self):
        """Gleicher state, anderer Verifier: der Challenge muss sich unterscheiden."""
        provider = XProvider(CREDS)
        first = provider.get_auth_url("https://app.example/cb", "same-state", code_verifier=VERIFIER)
        second = provider.get_auth_url("https://app.example/cb", "same-state", code_verifier=VERIFIER + "-anders")

        challenge_a = parse_qs(urlsplit(first).query)["code_challenge"][0]
        challenge_b = parse_qs(urlsplit(second).query)["code_challenge"][0]
        assert challenge_a != challenge_b

    @patch.object(XProvider, "_request")
    def test_exchange_code_replays_verifier(self, mock_request):
        mock_request.return_value = _make_response({"access_token": "tok", "expires_in": 7200})

        XProvider(CREDS).exchange_code("auth-code", "https://app.example/cb", code_verifier=VERIFIER)

        _, kwargs = mock_request.call_args
        assert kwargs["data"]["code_verifier"] == VERIFIER
        assert kwargs["data"]["grant_type"] == "authorization_code"

    def test_exchange_code_requires_verifier(self):
        with pytest.raises(OAuthError):
            XProvider(CREDS).exchange_code("auth-code", "https://app.example/cb")


class TestScopesAndLimits:
    def test_required_scopes(self):
        assert XProvider(CREDS).required_scopes == [
            "tweet.read",
            "tweet.write",
            "users.read",
            "offline.access",
        ]

    def test_caption_limit_is_280(self):
        assert XProvider(CREDS).max_caption_length == 280

    def test_no_media_types_on_free_tier(self):
        assert XProvider(CREDS).supported_media_types == []


class TestPublish:
    @patch.object(XProvider, "_request")
    def test_publishes_text(self, mock_request):
        mock_request.return_value = _make_response({"data": {"id": "1234567890"}})

        result = XProvider(CREDS).publish_post("tok", PublishContent(text="Hallo Welt"))

        _, kwargs = mock_request.call_args
        assert kwargs["json"] == {"text": "Hallo Welt"}
        assert result.platform_post_id == "1234567890"
        assert "1234567890" in result.url

    @patch.object(XProvider, "_request")
    def test_appends_link_when_missing(self, mock_request):
        mock_request.return_value = _make_response({"data": {"id": "1"}})

        XProvider(CREDS).publish_post("tok", PublishContent(text="Neues Buch", link_url="https://orbita-media.de"))

        _, kwargs = mock_request.call_args
        assert kwargs["json"]["text"] == "Neues Buch\nhttps://orbita-media.de"

    @patch.object(XProvider, "_request")
    def test_truncates_over_limit(self, mock_request):
        mock_request.return_value = _make_response({"data": {"id": "1"}})

        XProvider(CREDS).publish_post("tok", PublishContent(text="a" * 400))

        _, kwargs = mock_request.call_args
        assert len(kwargs["json"]["text"]) == 280

    def test_rejects_media(self):
        with pytest.raises(PublishError):
            XProvider(CREDS).publish_post(
                "tok", PublishContent(text="mit Bild", media_urls=["https://example.com/a.jpg"])
            )

    def test_rejects_empty_text(self):
        with pytest.raises(PublishError):
            XProvider(CREDS).publish_post("tok", PublishContent(text="   "))


class TestAnalytics:
    @patch.object(XProvider, "_request")
    def test_post_metrics_mapping(self, mock_request):
        mock_request.return_value = _make_response(
            {
                "data": {
                    "public_metrics": {
                        "like_count": 5,
                        "reply_count": 2,
                        "retweet_count": 3,
                        "quote_count": 1,
                    },
                    "non_public_metrics": {"impression_count": 100, "url_link_clicks": 7},
                }
            }
        )

        metrics = XProvider(CREDS).get_post_metrics("tok", "1")

        assert metrics.impressions == 100
        assert metrics.likes == 5
        assert metrics.comments == 2
        assert metrics.shares == 4  # Retweets plus Zitate
        assert metrics.clicks == 7

    def test_account_metrics_not_supported(self):
        """X Free Tier hat keine Konto-Kennzahlen; apps/analytics fängt das ab."""
        with pytest.raises(NotImplementedError):
            XProvider(CREDS).get_account_metrics("tok", (None, None))
