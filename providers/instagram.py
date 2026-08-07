"""Instagram Graph API provider implementation.

Instagram's API is accessed through the Facebook Graph API. Authentication
uses the Facebook OAuth flow with Instagram-specific scopes.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from urllib.parse import urlencode

from .base import SocialProvider
from .exceptions import APIError, OAuthError, PublishError
from .meta_business import pages_when_me_accounts_is_empty
from .meta_diagnostics import collect_diagnostics
from .meta_insights import fetch_insights_safe
from .meta_pages import SOURCE_ME_ACCOUNTS
from .types import (
    AccountMetrics,
    AccountProfile,
    AuthType,
    CommentResult,
    InboxMessage,
    MediaType,
    OAuthTokens,
    PostMetrics,
    PostType,
    PublishContent,
    PublishResult,
    RateLimitConfig,
    ReplyResult,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.facebook.com/v25.0"
OAUTH_URL = "https://www.facebook.com/v25.0/dialog/oauth"
TOKEN_URL = f"{BASE_URL}/oauth/access_token"
INSTAGRAM_ACCOUNT_INSIGHTS = [
    "reach",
    "views",
    "accounts_engaged",
    "total_interactions",
]
INSTAGRAM_MEDIA_INSIGHTS = [
    "reach",
    "views",
    "likes",
    "comments",
    "saved",
    "shares",
    "total_interactions",
]
# Felder der Seitenabfrage. ``instagram_business_account`` ist der Weg zur
# Instagram-Kennung, den die Anleitung nennt: "GET /{page-id}?fields=
# instagram_business_account" (Instagram Platform, "Instagram API with Facebook
# Login – Get Started").
PAGE_FIELDS = (
    "id,name,access_token,category,picture,"
    "instagram_business_account{id,username,name,profile_picture_url,followers_count,media_count}"
)
# Ausweichabfrage, siehe ``_accounts_via_connected_instagram``. Bewusst schmal
# gehalten: Für dieses Feld beschreibt die Referenz keine Unterfelder, deshalb
# werden nur ``id`` und ``username`` verlangt und alles Weitere am Konto selbst
# nachgeladen.
CONNECTED_PAGE_FIELDS = "id,name,access_token,category,picture,connected_instagram_account{id,username}"

# Feldleiter für den Ausweichweg über die einzelne Seite (siehe
# ``providers/meta_pages.py``). Dort wird jede Seite einzeln geholt, deshalb
# lohnt es sich, beide Verknüpfungsfelder in EINEM Aufruf zu verlangen statt
# hinterher ein zweites Mal zu fragen. Fällt ein Feldname weg, greift die
# nächste Zeile.
PAGE_FIELD_SETS_BY_ID = (
    (
        "id,name,access_token,category,picture,tasks,"
        "instagram_business_account{id,username,name,profile_picture_url,followers_count,media_count},"
        "connected_instagram_account{id,username}"
    ),
    PAGE_FIELDS,
    CONNECTED_PAGE_FIELDS,
    "id,name,access_token,category,picture",
)

# Feldleiter für die Notfall-Auflösung der Konto-Kennung (``_get_ig_user_id``).
# Bewusst schmal: Gebraucht wird nur die Kennung, nicht das ganze Konto.
IG_LOOKUP_FIELD_SETS = (
    "id,instagram_business_account{id},connected_instagram_account{id}",
    "id,instagram_business_account",
)

INSTAGRAM_MEDIA_FIELDS = [
    "id",
    "caption",
    "media_type",
    "media_product_type",
    "media_url",
    "thumbnail_url",
    "permalink",
    "timestamp",
    "like_count",
    "comments_count",
]

# Polling settings for container status checks
CONTAINER_POLL_INTERVAL = 2  # seconds
CONTAINER_POLL_MAX_ATTEMPTS = 60

# ``alt_text`` on POST /{ig-user-id}/media: "up to 1000 character, for an
# image. Only supported on a single image or image media in a carousel. Reels
# and stories are not supported." It belongs on each carousel child container,
# not on the parent CAROUSEL container.
MAX_ALT_TEXT_LENGTH = 1000

# A Graph API carousel holds 2 to 10 children. Sending more is rejected by the
# API; we stop earlier and say which slides would not have made it, because a
# six-slide carousel silently losing its closing slide is the exact failure
# this project already had on Bluesky.
MAX_CAROUSEL_ITEMS = 10

# ---------------------------------------------------------------------------
# Collaborators (Kollaborations-Beiträge)
# ---------------------------------------------------------------------------
# ``collaborators`` on POST /{ig-user-id}/media: "For Feed image, Reels and
# Carousels only. A list of up to 3 instagram usernames as collaborators."
# https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
#
# Why this field matters more than any other on this page: tagging someone does
# NOT put the post in front of their followers, collaborating does. Instagram
# says so itself: "If someone tags or mentions you in a photo or video, that
# photo or video won't be shared with your followers. If you collaborate on a
# post, that post will be shared with your followers."
# https://help.instagram.com/5861247717337470
#
# Two traps:
#   1. It takes USERNAMES, not numeric IDs (unlike branded_content_sponsor_ids).
#   2. Like audio_configuration it must go over the wire as a JSON *string*.
#      A native Python list in the JSON body is accepted with a 200 and the
#      post simply appears without collaborators — it does not fail loudly.
#
# The invitation still has to be accepted by the other account. Until then the
# post is live on our profile only; there is no API for the acceptance.
MAX_COLLABORATORS = 3

# ---------------------------------------------------------------------------
# Instagram Audio API
# ---------------------------------------------------------------------------
# Opened by Meta on 2026-06-01 for apps using Facebook Login. It is documented
# on its own page, NOT in the Media reference – reading only the reference
# leads to the wrong conclusion, because ``POST /{ig-user-id}/media`` lists 21
# parameters and none of them selects a track. The one audio-ish field there,
# ``audio_name``, merely *renames* the original sound already inside the
# uploaded video ("You can only rename once"); it attaches nothing.
#
#   Search / trending:  GET /ig_audio?audio_type=music[&search_query=…]
#                       "if no search query is provided, trending audio is returned"
#   Metadata:           GET /{ig-audio-id}
#   Attach:             audio_configuration={"audio_id":…,"audio_volume":…,"video_volume":…}
#                       on the REELS container, at creation time only.
#
# https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api/
AUDIO_ENDPOINT = f"{BASE_URL}/ig_audio"
AUDIO_TYPE_MUSIC = "music"
AUDIO_TYPE_ORIGINAL_SOUND = "original_sound"
AUDIO_TYPES = (AUDIO_TYPE_MUSIC, AUDIO_TYPE_ORIGINAL_SOUND)

# How many tracks one catalogue call returns. Meta paginates; the composer
# shows a single page, so keep it short enough to stay readable.
DEFAULT_AUDIO_LIMIT = 25
MAX_AUDIO_LIMIT = 50

# Volume defaults, deliberately NOT Meta's (audio 100 / video 100).
#
# Our reels carry a narrator. The documented mix knob is per-track: 100 is
# "full volume", 0 is muted, so the values behave like a linear amplitude
# percentage. A music bed under speech belongs 12 LU below the voice
# (docs/MUSIK-UND-TON.md §5), and 10^(-12/20) = 0.251 – hence 25 for the
# platform sound while the video (our voice) stays at 100. A reel WITHOUT
# narration inverts this; the composer offers that as a second preset.
DEFAULT_AUDIO_VOLUME = 25
DEFAULT_VIDEO_VOLUME = 100
MIN_VOLUME = 0
MAX_VOLUME = 100


def clamp_volume(value, default: int) -> int:
    """Coerce a volume to the documented 0-100 range, falling back to ``default``."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(MIN_VOLUME, min(number, MAX_VOLUME))


def build_audio_configuration(extra: dict | None) -> dict | None:
    """Build the ``audio_configuration`` object from per-platform settings.

    Reads ``audio_id`` (the only required field) plus the two optional volume
    knobs out of ``PlatformPost.platform_extra``. Returns ``None`` when no
    sound was chosen, which is the normal case: embedded CC0 music stays the
    default and a platform sound is the deliberate exception.

    ``should_loop_audio`` is deliberately not supported. It appears only in the
    documentation's code sample and is missing from the field table, so it is
    undocumented and must not be treated as guaranteed.
    """
    if not extra:
        return None
    audio_id = str(extra.get("audio_id") or "").strip()
    if not audio_id:
        return None
    return {
        "audio_id": audio_id,
        "audio_volume": clamp_volume(extra.get("audio_volume"), DEFAULT_AUDIO_VOLUME),
        "video_volume": clamp_volume(extra.get("video_volume"), DEFAULT_VIDEO_VOLUME),
    }


def build_collaborators(extra: dict | None) -> list[str] | None:
    """Build the ``collaborators`` list from per-platform settings.

    Reads ``collaborators`` out of ``PlatformPost.platform_extra``. Accepts a
    list or a comma-separated string, because both shapes reach us: the
    composer sends a list, an imported plan sends one field of text.

    Normalises what humans type: a leading ``@``, stray whitespace, an empty
    entry, the same name twice. Keeps the original order, because the first
    name is the one the author reads first in the post header.

    Returns ``None`` when nobody was named, which is the normal case. Anything
    beyond the documented maximum of three is dropped rather than sent: Graph
    rejects the whole container for a fourth name and would take a fully
    produced reel down with it.
    """
    if not extra:
        return None
    raw = extra.get("collaborators")
    if raw is None:
        return None
    if isinstance(raw, str):
        candidates = raw.replace(",", " ").split()
    else:
        try:
            candidates = list(raw)
        except TypeError:
            return None

    names: list[str] = []
    for candidate in candidates:
        name = str(candidate or "").strip().lstrip("@").strip()
        if not name:
            continue
        if name.lower() in {existing.lower() for existing in names}:
            continue
        names.append(name)

    if not names:
        return None
    if len(names) > MAX_COLLABORATORS:
        logger.warning(
            "Instagram: %d collaborators given, Graph allows %d, dropping %s",
            len(names),
            MAX_COLLABORATORS,
            ", ".join(names[MAX_COLLABORATORS:]),
        )
        names = names[:MAX_COLLABORATORS]
    return names


class InstagramProvider(SocialProvider):
    """Instagram Graph API provider (via Facebook Graph API v25.0)."""

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
        return "Instagram"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.OAUTH2

    @property
    def max_caption_length(self) -> int:
        return 2200

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.IMAGE, PostType.CAROUSEL, PostType.REEL, PostType.STORY]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG, MediaType.GIF, MediaType.MP4, MediaType.MOV]

    @property
    def max_media_per_post(self) -> int | None:
        # Graph API: a carousel holds 2 to 10 children, images and videos mixed.
        return MAX_CAROUSEL_ITEMS

    @property
    def required_scopes(self) -> list[str]:
        scopes = [
            "instagram_basic",
            "instagram_content_publish",
            "instagram_manage_comments",
            "instagram_manage_insights",
            "pages_show_list",
            "pages_read_engagement",
        ]
        # ``business_management`` wird NICHT standardmässig verlangt. Es ist der
        # einzige Weg für die Dialog-Option "alle aktuellen und zukünftigen
        # Seiten" (siehe ``providers/meta_business.py``), aber es ist auch eine
        # Berechtigung mehr im Dialog. Der Weg, der nachweislich funktioniert
        # ("nur aktuelle Seiten auswählen"), kommt ohne sie aus und darf durch
        # sie nicht gefährdet werden. Umlegbar über META_REQUEST_BUSINESS_SCOPE,
        # ohne dass dafür Code geändert werden muss.
        if self.include_business_scope:
            scopes.append("business_management")
        return scopes

    @property
    def rate_limits(self) -> RateLimitConfig:
        return RateLimitConfig(
            requests_per_hour=200,
            requests_per_day=5000,
            publish_per_day=100,
            extra={"published_posts_per_24h": 100},
        )

    # ------------------------------------------------------------------
    # OAuth (uses Facebook OAuth flow)
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str, code_verifier: str | None = None) -> str:
        params = {
            "client_id": self.credentials["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(self.required_scopes),
            "response_type": "code",
        }
        return f"{OAUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuthTokens:
        resp = self._request(
            "POST",
            TOKEN_URL,
            params={
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
            },
        )
        data = resp.json()
        if "access_token" not in data:
            raise OAuthError(
                "Instagram token exchange failed",
                platform=self.platform_name,
                raw_response=data,
            )
        return OAuthTokens(
            access_token=data["access_token"],
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            raw_response=data,
        )

    def refresh_token(self, short_lived_token: str) -> OAuthTokens:
        """Exchange short-lived token for a long-lived one (same as Facebook)."""
        resp = self._request(
            "GET",
            f"{BASE_URL}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "fb_exchange_token": short_lived_token,
            },
        )
        data = resp.json()
        if "access_token" not in data:
            raise OAuthError(
                "Instagram long-lived token exchange failed",
                platform=self.platform_name,
                raw_response=data,
            )
        return OAuthTokens(
            access_token=data["access_token"],
            expires_in=data.get("expires_in"),
            token_type=data.get("token_type", "Bearer"),
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        ig_user_id = self._get_ig_user_id(access_token)
        resp = self._request(
            "GET",
            f"{BASE_URL}/{ig_user_id}",
            access_token=access_token,
            params={"fields": "id,username,name,profile_picture_url,followers_count,media_count"},
        )
        data = resp.json()
        return AccountProfile(
            platform_id=data["id"],
            name=data.get("name", ""),
            handle=data.get("username"),
            avatar_url=data.get("profile_picture_url"),
            follower_count=data.get("followers_count", 0),
            extra=data,
        )

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def get_user_pages(self, access_token: str) -> list[dict]:
        """Fetch linked Instagram Business accounts for Facebook-login OAuth.

        The user authenticates through Facebook, but the connected account in
        Brightbean should be the Instagram Business account selected from the
        Facebook Pages the user manages.

        Bleibt ``/me/accounts`` leer, greifen zwei Ausweichwege nacheinander:
        die im Dialog angehakten Seitenkennungen aus dem Token
        (``providers/meta_pages.py``) und, wenn auch die fehlen, die Seiten der
        Business-Portfolios (``providers/meta_business.py``). Der zweite Weg ist
        der Fall "Alle aktuellen und zukünftigen Seiten", bei dem das Token
        keine einzelne Seite nennt. Welcher Weg gegriffen hat, steht im
        Protokoll.
        """
        resp = self._request(
            "GET",
            f"{BASE_URL}/me/accounts",
            access_token=access_token,
            params={"fields": PAGE_FIELDS},
        )
        data = resp.json()
        if "error" in data:
            logger.error("Instagram /me/accounts error: %s", data["error"])
            raise APIError(
                f"Failed to fetch Instagram accounts: {data['error'].get('message', 'Unknown error')}",
                platform=self.platform_name,
                raw_response=data,
            )

        pages = data.get("data", [])
        source = SOURCE_ME_ACCOUNTS
        if not pages:
            pages, source = pages_when_me_accounts_is_empty(
                self._request,
                base_url=BASE_URL,
                access_token=access_token,
                field_sets=PAGE_FIELD_SETS_BY_ID,
                app_id=self.credentials.get("client_id", ""),
                app_secret=self.credentials.get("client_secret", ""),
                label="Instagram",
            )
        logger.info("Instagram: %d Seite(n) über %s gefunden", len(pages), source)

        accounts: list[dict] = []
        for page in pages:
            ig_account = page.get("instagram_business_account")
            if not ig_account:
                continue

            picture_url = ig_account.get("profile_picture_url")
            if not picture_url and "picture" in page and "data" in page["picture"]:
                picture_url = page["picture"]["data"].get("url")

            username = ig_account.get("username", "")
            name = ig_account.get("name") or username or page.get("name", "")
            account = {
                "id": str(ig_account["id"]),
                "name": name,
                "handle": username,
                "category": page.get("category", ""),
                "picture": picture_url,
                "followers_count": ig_account.get("followers_count", 0),
                "page_id": page.get("id"),
                "page_name": page.get("name", ""),
            }
            page_token = page.get("access_token")
            if page_token:
                account["access_token"] = page_token
            accounts.append(account)

        if pages and not accounts:
            # Seiten da, aber keine mit ``instagram_business_account``: bevor
            # der Nutzer eine Fehlmeldung bekommt, wird das zweite Seitenfeld
            # geprüft, das dieselbe Verknüpfung tragen kann.
            if source != SOURCE_ME_ACCOUNTS:
                # Beide Ausweichwege haben die Verknüpfungsfelder schon verlangt
                # (dieselbe Feldleiter), ein zweiter Rundgang zum Graph wäre
                # verschenkt.
                accounts = self._accounts_from_connected_field(access_token, pages)
            else:
                accounts = self._accounts_via_connected_instagram(access_token, pages)
        return accounts

    def _accounts_via_connected_instagram(self, access_token: str, pages: list[dict]) -> list[dict]:
        """Zweiter Versuch über das Seitenfeld ``connected_instagram_account``.

        Der Graph führt zwei Felder für die Instagram-Verknüpfung einer Seite,
        und die Referenz unterscheidet sie danach, WIE die Verknüpfung entstanden
        ist: ``instagram_business_account`` ist das "Instagram account linked to
        page during Instagram business conversion flow",
        ``connected_instagram_account`` das "Instagram account connected to page
        via page settings" (Graph API Reference, Page). Wer sein Konto also nicht
        über den Umwandlungsablauf, sondern in den Seiteneinstellungen bzw. im
        Business-Portfolio verbunden hat, hat unter Umständen nur das zweite Feld
        gefüllt – und fiel bisher lautlos durch.

        Der Aufruf bleibt bewusst eigenständig und abgesichert: Scheitert er,
        bleibt es bei der leeren Liste, statt die ganze Anbindung mitzureissen.
        """
        try:
            data = self._request(
                "GET",
                f"{BASE_URL}/me/accounts",
                access_token=access_token,
                params={"fields": CONNECTED_PAGE_FIELDS},
            ).json()
        except APIError as exc:
            logger.warning("Instagram: connected_instagram_account nicht abfragbar: %s", exc)
            return []

        return self._accounts_from_connected_field(access_token, data.get("data", []))

    def _accounts_from_connected_field(self, access_token: str, pages: list[dict]) -> list[dict]:
        """Kontoliste aus Seiten bauen, die ``connected_instagram_account`` schon tragen.

        Weil dieses Feld auch auf ein PRIVATES Instagram-Konto zeigen kann, mit
        dem sich nichts veröffentlichen liesse, wird jeder Treffer nachgeprüft –
        nur ein professionelles Konto antwortet auf seine Profilfelder.
        """
        accounts: list[dict] = []
        for page in pages:
            ig_account = page.get("connected_instagram_account") or {}
            ig_id = str(ig_account.get("id") or "")
            if not ig_id:
                continue

            profile = self._get_profile_fields(access_token, ig_id) or {}
            username = profile.get("username") or ig_account.get("username") or ""
            if not username:
                logger.warning(
                    "Instagram: Seite %s ist mit Konto %s verknüpft, das Konto liefert aber keine "
                    "Profilfelder – vermutlich kein professionelles Konto, wird nicht angeboten",
                    page.get("id"),
                    ig_id,
                )
                continue

            picture_url = profile.get("profile_picture_url")
            if not picture_url and "picture" in page and "data" in page["picture"]:
                picture_url = page["picture"]["data"].get("url")

            account = {
                "id": ig_id,
                "name": profile.get("name") or username or page.get("name", ""),
                "handle": username,
                "category": page.get("category", ""),
                "picture": picture_url,
                "followers_count": profile.get("followers_count", 0),
                "page_id": page.get("id"),
                "page_name": page.get("name", ""),
                "link_source": "connected_instagram_account",
            }
            page_token = page.get("access_token")
            if page_token:
                account["access_token"] = page_token
            accounts.append(account)

        if accounts:
            logger.info(
                "Instagram: %d Konto/Konten nur über connected_instagram_account gefunden",
                len(accounts),
            )
        return accounts

    def diagnose_pages(self, access_token: str) -> dict:
        """Erhebt, was der Graph zu diesem Zugang sagt (ohne Zugangstoken).

        Wird vom OAuth-Rückweg aufgerufen, wenn kein Konto gefunden wurde.
        """
        return collect_diagnostics(
            self._request,
            base_url=BASE_URL,
            access_token=access_token,
            app_id=self.credentials.get("client_id", ""),
            app_secret=self.credentials.get("client_secret", ""),
        )

    # ------------------------------------------------------------------
    # Publishing (two-step container flow)
    # ------------------------------------------------------------------

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        ig_user_id = content.extra.get("ig_user_id") or self._get_ig_user_id(access_token)

        if content.post_type == PostType.CAROUSEL:
            return self._publish_carousel(access_token, ig_user_id, content)
        return self._publish_single(access_token, ig_user_id, content)

    def _publish_single(self, access_token: str, ig_user_id: str, content: PublishContent) -> PublishResult:
        """Publish a single image, reel, or story."""
        payload: dict = {}

        if content.text:
            payload["caption"] = content.text

        audio_configuration = build_audio_configuration(content.extra)

        if content.post_type in (PostType.REEL, PostType.VIDEO):
            # Instagram no longer supports standalone feed videos: a single
            # video is published as a Reel. PostType.VIDEO (the engine's
            # fallback for a lone video asset) must take the REELS path too,
            # otherwise it falls through to the IMAGE branch and the .mp4 is
            # sent as image_url ("The image format is not supported").
            payload["media_type"] = "REELS"
            payload["video_url"] = content.media_urls[0]
            if audio_configuration:
                # Sent as a JSON *string*, exactly as the documented curl call
                # does (-d 'audio_configuration={…}'). Graph does not reliably
                # unpack a nested object out of a JSON body.
                payload["audio_configuration"] = json.dumps(audio_configuration)
        elif content.post_type == PostType.STORY:
            if content.media_urls and content.media_urls[0].endswith((".mp4", ".mov")):
                payload["media_type"] = "STORIES"
                payload["video_url"] = content.media_urls[0]
            else:
                payload["media_type"] = "STORIES"
                payload["image_url"] = content.media_urls[0]
        else:
            # Default IMAGE
            payload["image_url"] = content.media_urls[0]
            # Alt text is image-only on Instagram — reels and stories reject it,
            # so it is set on this branch alone.
            alt_text = content.alt_text_for(0, MAX_ALT_TEXT_LENGTH)
            if alt_text:
                payload["alt_text"] = alt_text

        if audio_configuration and "audio_configuration" not in payload:
            # A platform sound only attaches to a reel, at creation time. Rather
            # than fail an otherwise valid post we drop the setting and say so:
            # a leftover sound on an image post is a composer slip, not a reason
            # to lose the publish.
            logger.warning(
                "Instagram: ignoring audio_id %s on a %s post, the Audio API attaches sound to reels only",
                audio_configuration.get("audio_id"),
                content.post_type.value,
            )

        collaborators = build_collaborators(content.extra)
        if collaborators:
            if content.post_type == PostType.STORY:
                # "For Feed image, Reels and Carousels only". Sending it on a
                # story container is rejected, so it is dropped with a word
                # rather than losing the story.
                logger.warning(
                    "Instagram: ignoring collaborators %s on a story, Graph allows them on feed image, reels and carousels only",
                    ", ".join(collaborators),
                )
            else:
                # JSON *string*, same reason as audio_configuration: a native
                # list in the body is accepted with a 200 and silently ignored.
                payload["collaborators"] = json.dumps(collaborators)

        # Step 1: create container
        container_id, audio_dropped = self._create_container_with_audio(access_token, ig_user_id, payload)

        # Step 2: wait for container to be ready
        self._wait_for_container(access_token, container_id)

        # Step 3: publish
        result_extra: dict = {}
        if audio_configuration and "audio_configuration" in payload:
            result_extra["audio_id"] = audio_configuration["audio_id"]
        if audio_dropped:
            result_extra["audio_dropped"] = True
        return self._publish_container(access_token, ig_user_id, container_id, result_extra=result_extra)

    def _publish_carousel(self, access_token: str, ig_user_id: str, content: PublishContent) -> PublishResult:
        """Publish a carousel post with multiple media items."""
        if len(content.media_urls) > MAX_CAROUSEL_ITEMS:
            raise PublishError(
                f"Instagram carousels hold at most {MAX_CAROUSEL_ITEMS} items "
                f"(got {len(content.media_urls)}); split the post instead of losing slides",
                platform=self.platform_name,
            )

        child_ids: list[str] = []

        for index, url in enumerate(content.media_urls):
            is_video = url.lower().endswith((".mp4", ".mov"))
            child_payload: dict = {
                "is_carousel_item": True,
            }
            if is_video:
                child_payload["media_type"] = "VIDEO"
                child_payload["video_url"] = url
            else:
                child_payload["image_url"] = url
                # One description per slide — the index keeps it on the image
                # it belongs to. Video children don't accept alt_text.
                alt_text = content.alt_text_for(index, MAX_ALT_TEXT_LENGTH)
                if alt_text:
                    child_payload["alt_text"] = alt_text

            child_id = self._create_container(access_token, ig_user_id, child_payload)
            self._wait_for_container(access_token, child_id)
            child_ids.append(child_id)

        # Create carousel container
        carousel_payload: dict = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
        }
        if content.text:
            carousel_payload["caption"] = content.text

        collaborators = build_collaborators(content.extra)
        if collaborators:
            # Belongs on the parent container only. The children are staged
            # uploads and carry no authorship, exactly like alt_text sits on
            # the child and the caption on the parent.
            carousel_payload["collaborators"] = json.dumps(collaborators)

        carousel_id = self._create_container(access_token, ig_user_id, carousel_payload)
        self._wait_for_container(access_token, carousel_id)

        return self._publish_container(access_token, ig_user_id, carousel_id)

    def _create_container_with_audio(self, access_token: str, ig_user_id: str, payload: dict) -> tuple[str, bool]:
        """Create the container, retrying once without the platform sound.

        Meta hands third parties a subset of the in-app catalogue ("the
        available selection may vary from what appears in the native app") and
        that subset moves. A track that has since been withdrawn would
        otherwise take a fully produced reel down with it, so the documented
        advice is to fall back to publishing without sound. The retry is safe:
        a container is only a staged upload, an abandoned one is never
        published.

        Returns ``(container_id, audio_dropped)``.
        """
        try:
            return self._create_container(access_token, ig_user_id, payload), False
        except (APIError, PublishError) as exc:
            if "audio_configuration" not in payload:
                raise
            logger.warning(
                "Instagram: container rejected with audio_configuration=%s, retrying without sound (%s)",
                payload["audio_configuration"],
                exc,
            )
            retry_payload = {k: v for k, v in payload.items() if k != "audio_configuration"}
            return self._create_container(access_token, ig_user_id, retry_payload), True

    def _create_container(self, access_token: str, ig_user_id: str, payload: dict) -> str:
        resp = self._request(
            "POST",
            f"{BASE_URL}/{ig_user_id}/media",
            access_token=access_token,
            json=payload,
        )
        data = resp.json()
        container_id = data.get("id")
        if not container_id:
            raise PublishError(
                "Failed to create Instagram media container",
                platform=self.platform_name,
                raw_response=data,
            )
        return container_id

    def _wait_for_container(self, access_token: str, container_id: str) -> None:
        """Poll container status until FINISHED or error."""
        for _ in range(CONTAINER_POLL_MAX_ATTEMPTS):
            resp = self._request(
                "GET",
                f"{BASE_URL}/{container_id}",
                access_token=access_token,
                params={"fields": "status_code,status"},
            )
            data = resp.json()
            status = data.get("status_code", "")

            if status == "FINISHED":
                return
            if status == "ERROR":
                raise PublishError(
                    f"Instagram container failed: {data.get('status', 'unknown error')}",
                    platform=self.platform_name,
                    raw_response=data,
                )

            time.sleep(CONTAINER_POLL_INTERVAL)

        raise PublishError(
            "Instagram container processing timed out",
            platform=self.platform_name,
        )

    def _publish_container(
        self,
        access_token: str,
        ig_user_id: str,
        container_id: str,
        *,
        result_extra: dict | None = None,
    ) -> PublishResult:
        resp = self._request(
            "POST",
            f"{BASE_URL}/{ig_user_id}/media_publish",
            access_token=access_token,
            json={"creation_id": container_id},
        )
        data = resp.json()
        media_id = data.get("id", "")
        return PublishResult(
            platform_post_id=media_id,
            url=f"https://www.instagram.com/p/{media_id}/",
            extra={**data, **(result_extra or {})},
        )

    # ------------------------------------------------------------------
    # Audio (Instagram Audio API, Facebook Login only)
    # ------------------------------------------------------------------

    def list_audio(
        self,
        access_token: str,
        *,
        audio_type: str = AUDIO_TYPE_MUSIC,
        search_query: str = "",
        ig_user_id: str | None = None,
        limit: int = DEFAULT_AUDIO_LIMIT,
    ) -> list[dict]:
        """Return audio tracks that can be attached to a reel.

        Without ``search_query`` this is the trending list, verbatim from the
        docs: "When retrieving audio, if no search query is provided, trending
        audio is returned." With one it is a catalogue search.

        Two limits are worth knowing before trusting a result: the catalogue
        only contains what Meta "has been authorized for third party use", and
        it differs from the app. A track going viral in the app may simply not
        be here, which is why the composer treats sound as optional.
        """
        if audio_type not in AUDIO_TYPES:
            raise ValueError(f"audio_type must be one of {AUDIO_TYPES}, got {audio_type!r}")

        params: dict = {
            "audio_type": audio_type,
            "limit": max(1, min(int(limit), MAX_AUDIO_LIMIT)),
        }
        user_id = ig_user_id or self.credentials.get("ig_user_id")
        if user_id:
            params["user_id"] = user_id
        query = (search_query or "").strip()
        if query:
            params["search_query"] = query

        resp = self._request("GET", AUDIO_ENDPOINT, access_token=access_token, params=params)
        data = resp.json()
        items = data.get("data", data if isinstance(data, list) else [])
        tracks = [self._normalize_audio(item) for item in items if isinstance(item, dict)]
        return [t for t in tracks if t["id"]]

    def get_audio(self, access_token: str, audio_id: str) -> dict:
        """Fetch one track's metadata by its ``ig-audio-id``."""
        resp = self._request(
            "GET",
            f"{BASE_URL}/{audio_id}",
            access_token=access_token,
        )
        return self._normalize_audio(resp.json())

    @staticmethod
    def _normalize_audio(item: dict) -> dict:
        """Flatten one catalogue entry into id / title / artist / duration.

        The Audio API page documents the endpoints and the attach parameter but
        not the exact response shape, so every field is read through a list of
        plausible keys and the untouched payload is kept in ``raw``. Nothing
        downstream may depend on a key we have not seen in the wild.
        """

        def _first(*keys: str) -> str:
            for key in keys:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, int | float):
                    return str(value)
            return ""

        artist = _first("artist", "artist_name", "display_artist", "owner_username")
        if not artist:
            owner = item.get("owner")
            if isinstance(owner, dict):
                artist = str(owner.get("username") or owner.get("name") or "").strip()

        duration_ms = item.get("duration_ms") or item.get("duration_in_ms") or item.get("duration")
        try:
            duration_ms = int(duration_ms) if duration_ms is not None else None
        except (TypeError, ValueError):
            duration_ms = None

        return {
            "id": _first("id", "audio_id", "audio_asset_id", "ig_audio_id"),
            "title": _first("title", "audio_title", "display_name", "name", "song_title"),
            "artist": artist,
            "duration_ms": duration_ms,
            "cover_url": _first("cover_url", "display_image_uri", "thumbnail_url", "image_url"),
            "raw": item,
        }

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def publish_comment(self, access_token: str, post_id: str, text: str) -> CommentResult:
        resp = self._request(
            "POST",
            f"{BASE_URL}/{post_id}/comments",
            access_token=access_token,
            params={"fields": "id"},
            json={"message": text},
        )
        data = resp.json()
        return CommentResult(platform_comment_id=data["id"], extra=data)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_post_metrics(self, access_token: str, post_id: str) -> PostMetrics:
        fields = self._get_media_fields(access_token, post_id)
        values, errors = fetch_insights_safe(
            self._request,
            platform=self.platform_name,
            endpoint=f"{BASE_URL}/{post_id}/insights",
            access_token=access_token,
            metrics=INSTAGRAM_MEDIA_INSIGHTS,
            endpoint_type="media",
        )
        likes = values.get("likes", fields.get("like_count", 0))
        comments = values.get("comments", fields.get("comments_count", 0))

        return PostMetrics(
            reach=values.get("reach", 0),
            likes=likes,
            comments=comments,
            saves=values.get("saved", 0),
            shares=values.get("shares", 0),
            video_views=values.get("views", 0),
            extra={
                "total_interactions": values.get("total_interactions", 0),
                "raw_fields": fields,
                "raw_insights": values,
                "insight_errors": errors,
            },
        )

    def get_account_metrics(self, access_token: str, date_range: tuple[datetime, datetime]) -> AccountMetrics:
        ig_user_id = self.credentials.get("ig_user_id", "me")
        since = int(date_range[0].timestamp())
        until = int(date_range[1].timestamp())
        values, errors = fetch_insights_safe(
            self._request,
            platform=self.platform_name,
            endpoint=f"{BASE_URL}/{ig_user_id}/insights",
            access_token=access_token,
            metrics=INSTAGRAM_ACCOUNT_INSIGHTS,
            base_params={
                "period": "day",
                "since": since,
                "until": until,
            },
            metric_params={
                "views": {"metric_type": "total_value"},
                "accounts_engaged": {"metric_type": "total_value"},
                "total_interactions": {"metric_type": "total_value"},
            },
            endpoint_type="account",
        )
        profile = self._get_profile_fields(access_token, ig_user_id)
        # ``None`` means the fetch FAILED (vs a real 0): leave followers unset so
        # _account_metrics_to_dict skips it and we don't poison the snapshot with 0.
        followers = profile.get("followers_count", 0) if profile is not None else None

        return AccountMetrics(
            reach=values.get("reach", 0),
            followers=followers,
            extra={
                "views": values.get("views", 0),
                "accounts_engaged": values.get("accounts_engaged", 0),
                "total_interactions": values.get("total_interactions", 0),
                "raw_insights": values,
                "insight_errors": errors,
            },
        )

    # ------------------------------------------------------------------
    # Inbox
    # ------------------------------------------------------------------

    def get_messages(self, access_token: str, since: datetime | None = None) -> list[InboxMessage]:
        ig_user_id = self.credentials.get("ig_user_id", "me")
        params: dict = {"fields": "id,participants,messages{id,message,from,created_time}"}
        if since:
            params["since"] = int(since.timestamp())

        resp = self._request(
            "GET",
            f"{BASE_URL}/{ig_user_id}/conversations",
            access_token=access_token,
            params=params,
        )
        conversations = resp.json().get("data", [])

        messages: list[InboxMessage] = []
        for convo in conversations:
            for msg in convo.get("messages", {}).get("data", []):
                sender = msg.get("from", {})
                messages.append(
                    InboxMessage(
                        platform_message_id=msg["id"],
                        sender_id=sender.get("id", ""),
                        sender_name=sender.get("name", sender.get("username", "")),
                        text=msg.get("message", ""),
                        timestamp=datetime.fromisoformat(msg["created_time"].replace("+0000", "+00:00")),
                        message_type="dm",
                        extra={"conversation_id": convo["id"]},
                    )
                )
        return messages

    def reply_to_message(self, access_token: str, message_id: str, text: str, extra: dict | None = None) -> ReplyResult:
        """Reply to a conversation. message_id should be the conversation ID."""
        resp = self._request(
            "POST",
            f"{BASE_URL}/{message_id}/messages",
            access_token=access_token,
            json={"message": text},
        )
        data = resp.json()
        return ReplyResult(platform_message_id=data.get("id", ""), extra=data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_ig_user_id(self, access_token: str) -> str:
        """Resolve the Instagram Business Account ID from the connected
        Facebook Page.

        The IG user ID can be stored in credentials to avoid an extra API call.
        """
        if "ig_user_id" in self.credentials:
            return self.credentials["ig_user_id"]

        # Get pages and find the one with an instagram_business_account
        resp = self._request(
            "GET",
            f"{BASE_URL}/me/accounts",
            access_token=access_token,
            params={"fields": IG_LOOKUP_FIELD_SETS[0]},
        )
        pages = resp.json().get("data", [])
        if not pages:
            # Derselbe Notnagel wie beim Verbinden. In der Praxis kommt dieser
            # Zweig kaum vor – Veröffentlichen, Zustandsprüfung und Auswertung
            # reichen die gespeicherte Kennung als ``ig_user_id`` durch. Wenn er
            # doch greift, darf er nicht an derselben Stelle scheitern wie
            # ``/me/accounts``: Seiten aus einem Business-Portfolio stehen dort
            # nicht drin (siehe ``providers/meta_business.py``).
            pages, _ = pages_when_me_accounts_is_empty(
                self._request,
                base_url=BASE_URL,
                access_token=access_token,
                field_sets=IG_LOOKUP_FIELD_SETS,
                app_id=self.credentials.get("client_id", ""),
                app_secret=self.credentials.get("client_secret", ""),
                label="Instagram",
            )
        for page in pages:
            ig_account = page.get("instagram_business_account") or page.get("connected_instagram_account")
            if ig_account:
                return ig_account["id"]

        raise APIError(
            "No Instagram Business Account found linked to any Facebook Page",
            platform=self.platform_name,
        )

    def _get_profile_fields(self, access_token: str, ig_user_id: str) -> dict | None:
        # Returns ``None`` on failure so callers can distinguish a failed fetch
        # from a successful one with no data (a genuine 0).
        try:
            return self._request(
                "GET",
                f"{BASE_URL}/{ig_user_id}",
                access_token=access_token,
                params={"fields": "id,username,name,profile_picture_url,followers_count,media_count"},
            ).json()
        except APIError as exc:
            logger.debug("Instagram profile fields unavailable for %s: %s", ig_user_id, exc)
            return None

    def _get_media_fields(self, access_token: str, media_id: str) -> dict:
        try:
            return self._request(
                "GET",
                f"{BASE_URL}/{media_id}",
                access_token=access_token,
                params={"fields": ",".join(INSTAGRAM_MEDIA_FIELDS)},
            ).json()
        except APIError as exc:
            logger.debug("Instagram media fields unavailable for %s: %s", media_id, exc)
            return {}
