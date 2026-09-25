"""Google Business Profile API provider implementation."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
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
)

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ACCOUNTS_API = "https://mybusinessaccountmanagement.googleapis.com/v1"
BUSINESS_INFO_API = "https://mybusinessbusinessinformation.googleapis.com/v1"
POSTS_API = "https://mybusiness.googleapis.com/v4"

#: Required by accounts.locations.list (v1); only what get_profile shows.
LOCATION_READ_MASK = "name,title,storefrontAddress,phoneNumbers"
#: v4 ``CallToAction.actionType`` values we send (GET_OFFER is deprecated).
#: The dialog labels: BOOK Reservieren, ORDER Online bestellen, SHOP Kaufen,
#: LEARN_MORE Weitere Informationen, SIGN_UP Anmelden, CALL Anrufen.
CTA_TYPES = frozenset({"BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"})
DEFAULT_LANGUAGE = "de"
V4_PARENT = re.compile(r"accounts/[0-9]+/locations/[0-9]+")


def location_number(value: str) -> str:
    """``2832…``, ``locations/2832…`` or ``accounts/1/locations/2832…`` → ``2832…``."""
    return (value or "").strip().rstrip("/").rsplit("/", 1)[-1]


class GoogleBusinessProvider(SocialProvider):
    """Google Business Profile provider.

    Uses Google OAuth 2.0.  The ``credentials`` dict must contain:

    - ``client_id``
    - ``client_secret``

    Optional:
    - ``account_id`` – Google Business account ID
    - ``location_id`` – Google Business location ID (``123``, ``locations/123``
      or ``accounts/1/locations/123``)

    Per-post ``extra`` keys: ``location_path`` (set by the engine from the
    connected account), ``gbp_cta`` (button type, see ``CTA_TYPES``),
    ``language_code``, ``topic_type``, ``event``, ``offer``.
    """

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def platform_name(self) -> str:
        return "Google Business"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.OAUTH2

    @property
    def max_caption_length(self) -> int:
        return 1500

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.TEXT, PostType.IMAGE]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG]

    @property
    def max_media_per_post(self) -> int | None:
        # localPosts documents "only one media item is supported"; a second
        # entry is rejected by the API rather than shown.
        return 1

    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/business.manage"]

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str, code_verifier: str | None = None) -> str:
        """Build the Google OAuth 2.0 authorization URL."""
        params = {
            "client_id": self.credentials["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.required_scopes),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuthTokens:
        """Exchange an authorization code for Google access/refresh tokens."""
        resp = self._request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Token exchange failed: {data.get('error_description', data['error'])}",
                platform=self.platform_name,
                raw_response=data,
            )
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    def refresh_token(self, refresh_token: str) -> OAuthTokens:
        """Refresh an expired Google access token."""
        resp = self._request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        data = resp.json()
        if "error" in data:
            raise OAuthError(
                f"Token refresh failed: {data.get('error_description', data['error'])}",
                platform=self.platform_name,
                raw_response=data,
            )
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=refresh_token,  # Google doesn't rotate refresh tokens
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope"),
            raw_response=data,
        )

    def revoke_token(self, access_token: str) -> bool:
        """Revoke a Google OAuth token."""
        try:
            self._request(
                "POST",
                REVOKE_URL,
                params={"token": access_token},
            )
            return True
        except Exception:
            logger.exception("Failed to revoke Google token")
            return False

    # ------------------------------------------------------------------
    # Account / location helpers
    # ------------------------------------------------------------------

    def _list_accounts(self, access_token: str) -> list[dict]:
        """All Business Profile accounts the grant can see, every page."""
        accounts: list[dict] = []
        page_token = None
        while True:
            params = {"pageSize": 20}
            if page_token:
                params["pageToken"] = page_token
            data = self._request("GET", f"{ACCOUNTS_API}/accounts", access_token=access_token, params=params).json()
            accounts.extend(data.get("accounts", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return accounts

    def _list_locations(self, access_token: str, account_name: str) -> list[dict]:
        """Locations of one account, every page.

        ``readMask`` is a *required* parameter of accounts.locations.list
        (Business Information API v1); without it Google answers 400 – which
        is what broke connecting before 25.09.2026.
        """
        locations: list[dict] = []
        page_token = None
        while True:
            params = {"readMask": LOCATION_READ_MASK, "pageSize": 100}
            if page_token:
                params["pageToken"] = page_token
            data = self._request(
                "GET",
                f"{BUSINESS_INFO_API}/{account_name}/locations",
                access_token=access_token,
                params=params,
            ).json()
            locations.extend(data.get("locations", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return locations

    def _find_location(self, access_token: str) -> tuple[str, dict]:
        """Return ``(account_name, location)`` of the location to post to.

        The first account in the list is often the personal account without
        any location, so every account is searched. A configured
        ``location_id`` (any spelling, see ``location_number``) wins.
        """
        wanted = location_number(self.credentials.get("location_id") or "")
        wanted_account = self.credentials.get("account_id") or ""
        if wanted_account and not wanted_account.startswith("accounts/"):
            wanted_account = f"accounts/{wanted_account}"
        accounts = [{"name": wanted_account}] if wanted_account else self._list_accounts(access_token)
        for account in accounts:
            for loc in self._list_locations(access_token, account["name"]):
                if not wanted or location_number(loc.get("name", "")) == wanted:
                    return account["name"], loc
        raise PublishError(
            "No Google Business location found"
            + (f" with id {wanted}" if wanted else "")
            + " – is the profile verified and owned by the connected Google account?",
            platform=self.platform_name,
        )

    def _post_parent(self, access_token: str, extra: dict) -> str:
        """v4 parent of a local post: ``accounts/{a}/locations/{l}``.

        localPosts live in the v4 API under the account, while the v1
        Business Information API names a location ``locations/{id}`` without
        it. Posting to ``v4/locations/{id}/localPosts`` is a 404 – the second
        bug found on 25.09.2026. The connected account stores the full v4
        path as its ``account_platform_id``; the engine hands it over as
        ``extra["location_path"]``.
        """
        path = extra.get("location_path") or ""
        if V4_PARENT.fullmatch(path):
            return path
        account_id = self.credentials.get("account_id") or ""
        location_id = self.credentials.get("location_id") or ""
        if account_id and location_id:
            account = account_id if account_id.startswith("accounts/") else f"accounts/{account_id}"
            return f"{account}/locations/{location_number(location_id)}"
        account_name, loc = self._find_location(access_token)
        return f"{account_name}/locations/{location_number(loc['name'])}"

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        """The location becomes the connected account.

        ``platform_id`` is the v4 parent ``accounts/{a}/locations/{l}`` so
        publishing needs no further lookup.
        """
        account_name, loc = self._find_location(access_token)
        address_lines = (loc.get("storefrontAddress") or {}).get("addressLines", [])
        address = ", ".join(address_lines)
        phone = (loc.get("phoneNumbers") or {}).get("primaryPhone", "")
        return AccountProfile(
            platform_id=f"{account_name}/locations/{location_number(loc['name'])}",
            name=loc.get("title") or loc.get("name", account_name),
            handle=None,
            extra={"address": address, "phone": phone, "account": account_name, "location": loc.get("name", "")},
        )

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def build_post_body(self, content: PublishContent) -> dict:
        """The LocalPost payload, without any network call (tested directly)."""
        if content.text and len(content.text) > self.max_caption_length:
            raise PublishError(
                f"Post text exceeds {self.max_caption_length} characters (got {len(content.text)})",
                platform=self.platform_name,
            )
        topic_type = content.extra.get("topic_type", "STANDARD")
        body: dict = {
            # Our profile is German; "en" as fallback marked every post as English.
            "languageCode": content.extra.get("language_code") or DEFAULT_LANGUAGE,
            "summary": content.text or "",
            "topicType": topic_type,
        }

        # Button. The link was passed through by the engine as
        # ``content.link_url`` and silently dropped until 25.09.2026 – a post
        # without its "Kaufen" button has no way to the book page.
        cta = (content.extra.get("gbp_cta") or "").upper()
        if cta and cta not in CTA_TYPES:
            raise PublishError(
                f"Unknown Google Business button {cta!r}; allowed: {', '.join(sorted(CTA_TYPES))}",
                platform=self.platform_name,
            )
        if cta == "CALL":
            body["callToAction"] = {"actionType": "CALL"}
        elif content.link_url:
            body["callToAction"] = {"actionType": cta or "LEARN_MORE", "url": content.link_url}
        elif cta:
            raise PublishError(
                f"Button {cta} needs a link (link_url)",
                platform=self.platform_name,
            )

        # Attach media. Alt text is deliberately not forwarded here: for a
        # LocalPost media item "sourceUrl is the only supported data field",
        # so there is nowhere to put an accessibility description.
        if content.media_urls:
            if len(content.media_urls) > 1:
                # localPosts takes exactly one media item; sending the whole
                # carousel would have the API reject the post outright.
                logger.warning(
                    "Google Business local posts carry one image: %d of %d attachments are dropped",
                    len(content.media_urls) - 1,
                    len(content.media_urls),
                )
            body["media"] = [{"mediaFormat": "PHOTO", "sourceUrl": content.media_urls[0]}]

        # EVENT type extras
        if topic_type == "EVENT" and content.extra.get("event"):
            body["event"] = content.extra["event"]

        # OFFER type extras
        if topic_type == "OFFER" and content.extra.get("offer"):
            body["offer"] = content.extra["offer"]
        return body

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        """Publish a local post to Google Business Profile."""
        body = self.build_post_body(content)
        parent = self._post_parent(access_token, content.extra)
        resp = self._request(
            "POST",
            f"{POSTS_API}/{parent}/localPosts",
            access_token=access_token,
            json=body,
        )
        data = resp.json()
        if data.get("state") == "REJECTED":
            raise PublishError(
                "Google rejected the local post (state REJECTED) – check text and image against the content policy",
                platform=self.platform_name,
                raw_response=data,
            )
        return PublishResult(
            platform_post_id=data.get("name", ""),
            url=data.get("searchUrl"),
            extra=data,
        )

    def delete_post(self, access_token: str, post_id: str) -> bool:
        """Delete a local post (``accounts/…/locations/…/localPosts/…``)."""
        self._request("DELETE", f"{POSTS_API}/{post_id}", access_token=access_token)
        return True

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_post_metrics(self, access_token: str, post_id: str) -> PostMetrics:
        """Views and button clicks of one local post.

        A LocalPost carries no numbers; they come from
        ``localPosts:reportInsights`` (v4). Reading ``searchActionMetrics``
        off the post itself returned 0 forever – the third bug found on
        25.09.2026.
        """
        parent, sep, _ = post_id.rpartition("/localPosts/")
        if not sep or not V4_PARENT.fullmatch(parent):
            raise PublishError(f"Not a local post name: {post_id!r}", platform=self.platform_name)
        now = datetime.now(UTC)
        body = {
            "localPostNames": [post_id],
            "basicRequest": {
                "metricRequests": [{"metric": "ALL"}],
                "timeRange": {
                    # Posts are archived after six months; 18 months is the
                    # longest range Google's insights accept.
                    "startTime": (now - timedelta(days=540)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        }
        data = self._request(
            "POST",
            f"{POSTS_API}/{parent}/localPosts:reportInsights",
            access_token=access_token,
            json=body,
        ).json()
        werte: dict[str, int] = {}
        for eintrag in data.get("localPostMetrics", []):
            if eintrag.get("localPostName") not in (None, post_id):
                continue
            for mv in eintrag.get("metricValues", []):
                total = (mv.get("totalValue") or {}).get("value")
                if total is None:
                    total = sum(int(d.get("value") or 0) for d in mv.get("dimensionalValues", []))
                werte[mv.get("metric", "")] = werte.get(mv.get("metric", ""), 0) + int(total or 0)
        views = werte.get("LOCAL_POST_VIEWS_SEARCH", 0)
        clicks = werte.get("LOCAL_POST_ACTIONS_CALL_TO_ACTION", 0)
        return PostMetrics(
            impressions=views,
            clicks=clicks,
            extra={"search_views": views, "cta_clicks": clicks, "raw": data},
        )
