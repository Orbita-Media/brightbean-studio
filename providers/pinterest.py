"""Pinterest API v5 provider."""

from __future__ import annotations

import base64
import logging
import os
from urllib.parse import urlencode

from .base import SocialProvider
from .exceptions import APIError, OAuthError, PublishError
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

AUTH_URL = "https://www.pinterest.com/oauth/"
API_BASE = os.environ.get("PINTEREST_API_BASE", "https://api.pinterest.com/v5")
TOKEN_URL = f"{API_BASE}/oauth/token"

# ``alt_text`` on POST /v5/pins is capped at 500 characters by the OpenAPI spec.
MAX_ALT_TEXT_LENGTH = 500

# Carousel pin. The official OpenAPI (pinterest/api-description, v5 5.28.0)
# declares ``PinMediaSourceImagesURL`` with ``minItems: 2`` and ``maxItems: 5``.
# Note the contradiction documented in docs/PLATTFORM-GRENZEN.md: a guide page
# claims organic pins were "simplified to image or video Pins", while the
# current spec still defines ``multiple_image_urls`` without a deprecation
# marker. Hence the fallback in _publish_image_pin.
MIN_CAROUSEL_ITEMS = 2
MAX_CAROUSEL_ITEMS = 5

# ``title`` per PinCreate is capped at 100 characters.
MAX_TITLE_LENGTH = 100


class PinterestProvider(SocialProvider):
    """Pinterest API v5 provider using OAuth 2.0."""

    def __init__(self, credentials: dict | None = None):
        creds = dict(credentials or {})
        # Normalize: accept app_id/app_secret as aliases for client_id/client_secret
        if "app_id" in creds and "client_id" not in creds:
            creds["client_id"] = creds.pop("app_id")
        if "app_secret" in creds and "client_secret" not in creds:
            creds["client_secret"] = creds.pop("app_secret")
        super().__init__(creds)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def platform_name(self) -> str:
        return "Pinterest"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.OAUTH2

    @property
    def max_caption_length(self) -> int:
        return 500

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.PIN]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG, MediaType.GIF, MediaType.MP4]

    @property
    def max_media_per_post(self) -> int | None:
        # A carousel pin carries up to five images. Until 05.08.2026 this
        # provider claimed 1 and pinned slide one of six with nothing but a log
        # line – the number came from our own code, not from the platform.
        # Sources in docs/PLATTFORM-GRENZEN.md.
        return MAX_CAROUSEL_ITEMS

    @property
    def required_scopes(self) -> list[str]:
        return ["user_accounts:read", "boards:read", "pins:read", "pins:write"]

    @property
    def rate_limits(self) -> RateLimitConfig:
        return RateLimitConfig(
            requests_per_hour=1000,
            requests_per_day=24000,
            publish_per_day=25,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _basic_auth_header(self) -> dict[str, str]:
        """Build HTTP Basic auth header for token endpoints."""
        client_id = self.credentials["client_id"]
        client_secret = self.credentials["client_secret"]
        encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str, code_verifier: str | None = None) -> str:
        params = {
            "client_id": self.credentials["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(self.required_scopes),
            "response_type": "code",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuthTokens:
        resp = self._request(
            "POST",
            TOKEN_URL,
            headers=self._basic_auth_header(),
            data={
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        body = resp.json()
        if "access_token" not in body:
            raise OAuthError(
                f"Pinterest token exchange failed: {body}",
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
            },
        )
        body = resp.json()
        if "access_token" not in body:
            raise OAuthError(
                f"Pinterest token refresh failed: {body}",
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

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        resp = self._request(
            "GET",
            f"{API_BASE}/user_account",
            access_token=access_token,
        )
        body = resp.json()
        return AccountProfile(
            platform_id=body.get("id", ""),
            name=body.get("business_name") or body.get("username", ""),
            handle=body.get("username"),
            avatar_url=body.get("profile_image"),
            follower_count=body.get("follower_count", 0),
            extra=body,
        )

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        board_id = content.extra.get("board_id")
        if not board_id:
            raise PublishError(
                "board_id is required in content.extra for Pinterest pins",
                platform=self.platform_name,
            )

        payload: dict = {
            "board_id": board_id,
            "description": (content.description or content.text or "")[: self.max_caption_length],
        }

        if content.title:
            payload["title"] = content.title[:MAX_TITLE_LENGTH]

        if content.link_url:
            payload["link"] = content.link_url

        # Pinterest carries exactly one alt text per pin – the multi-image
        # item schema has no per-image field. An explicit per-account value
        # from the composer wins; otherwise the first attachment's description
        # stands in for the pin.
        alt_text = str(content.extra.get("alt_text") or "").strip() or content.alt_text_for(0)
        if alt_text:
            payload["alt_text"] = alt_text[:MAX_ALT_TEXT_LENGTH]

        # Determine media source
        is_video = content.extra.get("is_video", False)

        if is_video:
            return self._publish_video_pin(access_token, content, payload)

        # Image pin
        if not content.media_urls:
            if content.media_files:
                raise PublishError(
                    "Pinterest image file upload not supported via this provider; "
                    "use media_urls with a hosted image URL instead",
                    platform=self.platform_name,
                )
            raise PublishError(
                "No media provided for Pinterest pin",
                platform=self.platform_name,
            )

        return self._publish_image_pin(access_token, content, payload)

    def _publish_image_pin(
        self,
        access_token: str,
        content: PublishContent,
        payload: dict,
    ) -> PublishResult:
        """Create an image pin – a carousel from two attachments upwards.

        Pinterest contradicts itself on carousels (see the note at
        MIN_CAROUSEL_ITEMS), so a rejected carousel falls back to a single
        image pin instead of failing the post. The fallback is recorded in the
        result, because a carousel that silently became one image is exactly
        the kind of shrinkage nobody notices.
        """
        urls = content.media_urls
        if len(urls) > MAX_CAROUSEL_ITEMS:
            raise PublishError(
                f"Pinterest zeigt höchstens {MAX_CAROUSEL_ITEMS} Bilder je Pin, dieser Pin "
                f"bringt {len(urls)} mit. Es wird weder gekürzt noch verteilt: für Pinterest "
                f"wird eine eigene, in sich geschlossene Fassung mit höchstens "
                f"{MAX_CAROUSEL_ITEMS} Folien gebraucht.",
                platform=self.platform_name,
                retryable=False,
            )

        if len(urls) < MIN_CAROUSEL_ITEMS:
            payload["media_source"] = {"source_type": "image_url", "url": urls[0]}
            return self._create_pin(access_token, payload)

        payload["media_source"] = {
            "source_type": "multiple_image_urls",
            # The cover card. Slide one is the hook, so it stays the cover.
            "index": 0,
            "items": [{"url": url} for url in urls],
        }
        try:
            return self._create_pin(access_token, payload)
        except APIError as fehler:
            if fehler.status_code != 400:
                raise
            logger.warning(
                "Pinterest lehnt den Karussell-Pin ab (%s), Rückfall auf ein Einzelbild",
                fehler,
            )

        payload["media_source"] = {"source_type": "image_url", "url": urls[0]}
        result = self._create_pin(access_token, payload)
        result.extra["carousel_rejected"] = True
        result.extra["carousel_dropped_images"] = len(urls) - 1
        return result

    def _create_pin(self, access_token: str, payload: dict) -> PublishResult:
        resp = self._request(
            "POST",
            f"{API_BASE}/pins",
            access_token=access_token,
            json=payload,
        )
        body = resp.json()
        pin_id = body.get("id", "")
        return PublishResult(
            platform_post_id=pin_id,
            url=f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            extra=body,
        )

    def _publish_video_pin(
        self,
        access_token: str,
        content: PublishContent,
        payload: dict,
    ) -> PublishResult:
        """Upload a video pin via the media endpoint."""
        # Step 1: Register media upload
        media_resp = self._request(
            "POST",
            f"{API_BASE}/media",
            access_token=access_token,
            json={"media_type": "video"},
        )
        media_body = media_resp.json()
        media_id = media_body.get("media_id", "")
        upload_url = media_body.get("upload_url")

        if upload_url and content.media_files:
            # Step 2: Upload video binary
            video_path = content.media_files[0]
            with open(video_path, "rb") as f:
                video_data = f.read()

            self._request(
                "PUT",
                upload_url,
                headers={"Content-Type": "video/mp4"},
                data=video_data,
                timeout=120.0,
            )

        # Step 3: Create pin referencing media_id
        payload["media_source"] = {
            "source_type": "video_id",
            "media_id": media_id,
        }
        resp = self._request(
            "POST",
            f"{API_BASE}/pins",
            access_token=access_token,
            json=payload,
        )
        body = resp.json()
        pin_id = body.get("id", "")
        return PublishResult(
            platform_post_id=pin_id,
            url=f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else None,
            extra=body,
        )

    # ------------------------------------------------------------------
    # Boards
    # ------------------------------------------------------------------

    def get_boards(self, access_token: str) -> list[dict]:
        """Fetch all boards for the authenticated account."""
        resp = self._request(
            "GET",
            f"{API_BASE}/boards",
            access_token=access_token,
        )
        body = resp.json()
        return body.get("items", [])

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_post_metrics(self, access_token: str, post_id: str) -> PostMetrics:
        resp = self._request(
            "GET",
            f"{API_BASE}/pins/{post_id}/analytics",
            access_token=access_token,
            params={
                "metric_types": "IMPRESSION,PIN_CLICK,SAVE,OUTBOUND_CLICK",
                "start_date": "2020-01-01",
                "end_date": "2099-12-31",
            },
        )
        body = resp.json()
        # Pinterest returns metrics as aggregated daily data
        all_data = body.get("all", {})
        return PostMetrics(
            impressions=all_data.get("IMPRESSION", 0),
            clicks=all_data.get("PIN_CLICK", 0),
            saves=all_data.get("SAVE", 0),
            extra={
                "outbound_clicks": all_data.get("OUTBOUND_CLICK", 0),
                "raw": body,
            },
        )

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def revoke_token(self, access_token: str) -> bool:
        # Pinterest does not support token revocation; tokens expire naturally.
        return False
