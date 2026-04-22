"""X (Twitter) API v2 provider with OAuth 2.0 PKCE.

Free-Tier-kompatibel: Nur Text-Tweets (500 Posts/Monat Limit).
Media-Upload würde den Paid-Tier erfordern und ist absichtlich nicht
implementiert — publish_post lehnt Media-Posts mit PublishError ab.

PKCE wird deterministisch aus dem OAuth-state-Parameter abgeleitet,
damit der stateless Provider-Ansatz (get_auth_url / exchange_code auf
verschiedenen Instanzen) ohne Session-Storage funktioniert. Die Ableitung
nutzt SHA-256(state) als code_verifier, method=plain.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime
from urllib.parse import urlencode

from .base import SocialProvider
from .exceptions import OAuthError, PublishError
from .types import (
    AccountProfile,
    AuthType,
    MediaType,
    OAuthTokens,
    PostMetrics,
    PostType,
    PublishContent,
    PublishResult,
    RateLimitConfig,
)

logger = logging.getLogger(__name__)

AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
REVOKE_URL = "https://api.twitter.com/2/oauth2/revoke"
API_BASE = "https://api.twitter.com/2"


def _derive_code_verifier(state: str) -> str:
    """Deterministisch 43 chars aus state ableiten (PKCE min length = 43)."""
    digest = hashlib.sha256(state.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


class XProvider(SocialProvider):
    """X (Twitter) API v2 provider using OAuth 2.0 with PKCE.

    The ``credentials`` dict requires:
    - ``client_id`` — OAuth 2.0 Client ID from developer.twitter.com
    - ``client_secret`` — OAuth 2.0 Client Secret (for Confidential Clients)
    """

    @property
    def platform_name(self) -> str:
        return "X (Twitter)"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.OAUTH2

    @property
    def max_caption_length(self) -> int:
        return 280

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.TEXT]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return []

    @property
    def required_scopes(self) -> list[str]:
        return ["tweet.read", "tweet.write", "users.read", "offline.access"]

    @property
    def rate_limits(self) -> RateLimitConfig:
        # Free tier limits (as of 2024): 500 writes/month, very low reads
        return RateLimitConfig(
            requests_per_hour=50,
            requests_per_day=100,
            publish_per_day=16,  # ~500/month averaged
        )

    # ------------------------------------------------------------------
    # OAuth helpers
    # ------------------------------------------------------------------

    def _basic_auth_header(self) -> dict[str, str]:
        client_id = self.credentials["client_id"]
        client_secret = self.credentials["client_secret"]
        encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    # ------------------------------------------------------------------
    # OAuth flow
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        code_verifier = _derive_code_verifier(state)
        params = {
            "response_type": "code",
            "client_id": self.credentials["client_id"],
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.required_scopes),
            "state": state,
            "code_challenge": code_verifier,
            "code_challenge_method": "plain",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, state: str | None = None) -> OAuthTokens:
        if not state:
            raise OAuthError(
                "X exchange_code requires state parameter for PKCE verification",
                platform=self.platform_name,
            )
        code_verifier = _derive_code_verifier(state)
        resp = self._request(
            "POST",
            TOKEN_URL,
            headers=self._basic_auth_header(),
            data={
                "code": code,
                "grant_type": "authorization_code",
                "client_id": self.credentials["client_id"],
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        body = resp.json()
        if "access_token" not in body:
            raise OAuthError(
                f"X token exchange failed: {body}",
                platform=self.platform_name,
                raw_response=body,
            )
        return OAuthTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_in=body.get("expires_in"),
            scope=body.get("scope"),
            raw_response=body,
        )

    def refresh_token(self, refresh_token: str) -> OAuthTokens:
        resp = self._request(
            "POST",
            TOKEN_URL,
            headers=self._basic_auth_header(),
            data={
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "client_id": self.credentials["client_id"],
            },
        )
        body = resp.json()
        if "access_token" not in body:
            raise OAuthError(
                f"X token refresh failed: {body}",
                platform=self.platform_name,
                raw_response=body,
            )
        return OAuthTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", refresh_token),
            expires_in=body.get("expires_in"),
            scope=body.get("scope"),
            raw_response=body,
        )

    def revoke_token(self, access_token: str) -> bool:
        try:
            self._request(
                "POST",
                REVOKE_URL,
                headers=self._basic_auth_header(),
                data={"token": access_token, "token_type_hint": "access_token"},
            )
            return True
        except Exception as exc:
            logger.warning("X token revocation failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        resp = self._request(
            "GET",
            f"{API_BASE}/users/me",
            access_token=access_token,
            params={"user.fields": "id,username,name,profile_image_url,public_metrics"},
        )
        body = resp.json()
        data = body.get("data", {})
        metrics = data.get("public_metrics", {})
        return AccountProfile(
            platform_id=data.get("id", ""),
            name=data.get("name", ""),
            handle=data.get("username"),
            avatar_url=data.get("profile_image_url"),
            follower_count=metrics.get("followers_count", 0),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        if content.media_urls or content.media_files:
            raise PublishError(
                "X Free Tier unterstützt nur Text-Tweets. Media-Upload erfordert den "
                "Paid-Tier ($200/Monat) und ist in Orbita Social bewusst nicht aktiviert.",
                platform=self.platform_name,
            )

        text = (content.text or "").strip()
        if content.link_url and content.link_url not in text:
            text = f"{text}\n{content.link_url}".strip()

        if not text:
            raise PublishError(
                "X: Tweet-Text darf nicht leer sein",
                platform=self.platform_name,
            )

        if len(text) > self.max_caption_length:
            text = text[: self.max_caption_length - 1] + "…"

        resp = self._request(
            "POST",
            f"{API_BASE}/tweets",
            access_token=access_token,
            json={"text": text},
        )
        body = resp.json()
        data = body.get("data", {})
        tweet_id = data.get("id", "")
        if not tweet_id:
            raise PublishError(
                f"X tweet creation returned no id: {body}",
                platform=self.platform_name,
                raw_response=body,
            )

        username = data.get("edit_history_tweet_ids") and self.credentials.get("_handle_hint") or "i/web"
        return PublishResult(
            platform_post_id=tweet_id,
            url=f"https://x.com/{username}/status/{tweet_id}" if username != "i/web" else f"https://x.com/i/web/status/{tweet_id}",
            extra=data,
        )

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_post_metrics(self, access_token: str, post_id: str) -> PostMetrics:
        resp = self._request(
            "GET",
            f"{API_BASE}/tweets/{post_id}",
            access_token=access_token,
            params={"tweet.fields": "public_metrics,non_public_metrics"},
        )
        body = resp.json()
        data = body.get("data", {})
        public = data.get("public_metrics", {})
        non_public = data.get("non_public_metrics", {})
        return PostMetrics(
            impressions=non_public.get("impression_count", 0),
            likes=public.get("like_count", 0),
            comments=public.get("reply_count", 0),
            shares=public.get("retweet_count", 0) + public.get("quote_count", 0),
            clicks=non_public.get("url_link_clicks", 0),
            extra={"raw": body},
        )
