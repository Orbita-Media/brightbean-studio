"""Bluesky / AT Protocol provider implementation."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from datetime import UTC, datetime

from .base import SocialProvider
from .exceptions import PublishError
from .types import (
    AccountProfile,
    AuthType,
    MediaType,
    OAuthTokens,
    PostType,
    PublishContent,
    PublishResult,
    RateLimitConfig,
)

logger = logging.getLogger(__name__)

DEFAULT_PDS_URL = "https://bsky.social"

# Bluesky has TWO image embeds, and the difference decides how many slides fit.
#
# ``app.bsky.embed.images`` – the long-standing one, ``maxLength: 4``.
# ``app.bsky.embed.gallery`` – added 03.06.2026 (atproto commit 41a561e,
#   "[APP-1983] New gallery embed type"), ``maxLength: 20`` with the schema
#   itself asking clients to hold at 10: "The schema-level maxLength of 20 is a
#   future-proof ceiling. Clients should currently enforce a soft limit of 10
#   items in authoring UIs." The official app renders it as a swipeable photo
#   carousel from five images upwards since version 1.123 (09.06.2026), and
#   ``app.bsky.feed.defs#postView`` lists ``app.bsky.embed.gallery#view`` among
#   its embed refs, so the AppView hands it to clients.
#
# So a six-slide carousel fits into ONE Bluesky post. Until 05.08.2026 this
# provider only knew the four-image embed and continued the rest as replies to
# our own post – that is gone. A post has to stand on its own: a continuation
# reply turns one statement into two records and the second one gets read out
# of context or not at all. Anything that does not fit fails loudly instead
# (see publish_post); the fitting version is produced upstream, per channel.
MAX_EMBED_IMAGES = 4
MAX_GALLERY_IMAGES = 10

# app.bsky.embed.images#image declares ``alt`` as required with no length
# constraint; we still bound it so one runaway description can't blow the
# record size. app.bsky.embed.video#main caps ``alt`` at 1000 graphemes –
# len() counts code points and a grapheme is never fewer than one code point,
# so truncating to 1000 characters always stays inside that limit.
MAX_ALT_TEXT_LENGTH = 2000
MAX_VIDEO_ALT_TEXT_LENGTH = 1000


def _access_jwt_expires_in(access_jwt: str) -> int | None:
    """Return seconds until an AT Protocol access JWT expires, or None if unknown.

    The createSession / refreshSession responses don't include an expiry field;
    the only source of truth is the JWT's own `exp` claim. We decode the payload
    without verifying the signature – we're reading metadata from a token the
    server just minted over TLS, not making an authorization decision.
    """
    try:
        _, payload_b64, _ = access_jwt.split(".")
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        exp = int(payload["exp"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return max(0, exp - int(time.time()))


class BlueskyProvider(SocialProvider):
    """AT Protocol / Bluesky provider.

    Uses session-based authentication (app passwords), not OAuth.
    The ``credentials`` dict may contain:

    - ``pds_url`` – PDS base URL (defaults to ``https://bsky.social``)
    """

    def __init__(self, credentials: dict | None = None):
        super().__init__(credentials)
        self.pds_url: str = self.credentials.get("pds_url", DEFAULT_PDS_URL).rstrip("/")

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def platform_name(self) -> str:
        return "Bluesky"

    @property
    def auth_type(self) -> AuthType:
        return AuthType.SESSION

    @property
    def max_caption_length(self) -> int:
        return 300

    @property
    def supported_post_types(self) -> list[PostType]:
        return [PostType.TEXT, PostType.IMAGE, PostType.CAROUSEL, PostType.VIDEO]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG, MediaType.MP4]

    @property
    def max_media_per_post(self) -> int | None:
        # The gallery embed carries the whole carousel; the four-image embed is
        # only used below five slides, where it stays the better-supported form.
        return MAX_GALLERY_IMAGES

    @property
    def required_scopes(self) -> list[str]:
        return []  # session-based, no scopes

    @property
    def rate_limits(self) -> RateLimitConfig:
        return RateLimitConfig(
            requests_per_hour=5000,
            requests_per_day=35000,
        )

    # ------------------------------------------------------------------
    # OAuth stubs (not applicable for session auth)
    # ------------------------------------------------------------------

    def get_auth_url(self, redirect_uri: str, state: str, code_verifier: str | None = None) -> str:
        raise NotImplementedError("Bluesky uses session-based auth, not OAuth. Use create_session() instead.")

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> OAuthTokens:
        raise NotImplementedError("Bluesky uses session-based auth, not OAuth. Use create_session() instead.")

    # ------------------------------------------------------------------
    # Handle resolution
    # ------------------------------------------------------------------

    def resolve_handle(self, handle: str) -> str:
        """Resolve a Bluesky handle to a DID.

        Uses bsky.social for resolution regardless of PDS URL.
        """
        resp = self._request(
            "GET",
            f"{DEFAULT_PDS_URL}/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": handle},
        )
        data = resp.json()
        return data["did"]

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def create_session(self, handle: str, app_password: str) -> OAuthTokens:
        """Create an AT Protocol session using handle and app password.

        Returns an ``OAuthTokens`` with *accessJwt* as ``access_token`` and
        *refreshJwt* as ``refresh_token``.
        """
        resp = self._request(
            "POST",
            f"{self.pds_url}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
        )
        data = resp.json()
        return OAuthTokens(
            access_token=data["accessJwt"],
            refresh_token=data["refreshJwt"],
            expires_in=_access_jwt_expires_in(data["accessJwt"]),
            raw_response=data,
        )

    def refresh_token(self, refresh_token: str) -> OAuthTokens:
        """Refresh an AT Protocol session using the refresh JWT."""
        resp = self._request(
            "POST",
            f"{self.pds_url}/xrpc/com.atproto.server.refreshSession",
            access_token=refresh_token,
        )
        data = resp.json()
        return OAuthTokens(
            access_token=data["accessJwt"],
            refresh_token=data["refreshJwt"],
            expires_in=_access_jwt_expires_in(data["accessJwt"]),
            raw_response=data,
        )

    # ------------------------------------------------------------------
    # Token revocation
    # ------------------------------------------------------------------

    def revoke_token(self, access_token: str) -> bool:
        """Delete the AT Protocol session (logout)."""
        try:
            self._request(
                "POST",
                f"{self.pds_url}/xrpc/com.atproto.server.deleteSession",
                access_token=access_token,
            )
            return True
        except Exception:
            logger.exception("Failed to delete Bluesky session")
            return False

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    def get_profile(self, access_token: str) -> AccountProfile:
        """Fetch the authenticated user's Bluesky profile."""
        # Decode the DID from the JWT payload (middle segment) or use the
        # actor param. We call getProfile with the session's own DID stored
        # in the JWT.  Easier: use "actor=did:..." but we need the DID.
        # We can call getSession to retrieve the DID.
        session = self._request(
            "GET",
            f"{self.pds_url}/xrpc/com.atproto.server.getSession",
            access_token=access_token,
        ).json()
        did = session["did"]

        resp = self._request(
            "GET",
            f"{self.pds_url}/xrpc/app.bsky.actor.getProfile",
            params={"actor": did},
            access_token=access_token,
        )
        data = resp.json()
        handle = data.get("handle") or ""
        return AccountProfile(
            platform_id=data.get("did", did),
            name=data.get("displayName") or handle,
            handle=handle,
            avatar_url=data.get("avatar"),
            follower_count=data.get("followersCount", 0),
        )

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish_post(self, access_token: str, content: PublishContent) -> PublishResult:
        """Publish ONE post to Bluesky via com.atproto.repo.createRecord.

        Exactly one record is written, always. A carousel with more than
        ``MAX_EMBED_IMAGES`` slides does not get cut down and does not get
        continued in a reply – it fails here, because the fitting version is
        the job of whoever produced the post.
        """
        # Validate grapheme length
        grapheme_count = len(content.text) if content.text else 0
        if grapheme_count > self.max_caption_length:
            raise PublishError(
                f"Post text exceeds {self.max_caption_length} graphemes (got {grapheme_count})",
                platform=self.platform_name,
            )

        self._reject_overflowing_carousel(content)

        # Get session DID
        session = self._request(
            "GET",
            f"{self.pds_url}/xrpc/com.atproto.server.getSession",
            access_token=access_token,
        ).json()
        did = session["did"]
        handle = session.get("handle", "")

        # Blobs are uploaded before the record is created. Uploading is by far
        # the likeliest step to fail (size, network, PDS hiccup) and while
        # nothing is posted yet a failure costs nothing – the publisher just
        # retries.
        embed = self._build_embed(access_token, content)
        data = self._create_post_record(access_token, did=did, text=content.text or "", embed=embed)

        uri = data.get("uri", "")
        rkey = uri.split("/")[-1] if uri else ""
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None

        return PublishResult(
            platform_post_id=uri,
            url=post_url,
            extra=dict(data),
        )

    def _reject_overflowing_carousel(self, content: PublishContent) -> None:
        """Fail before anything is posted when the carousel does not fit.

        Not retryable: a second attempt sends the same attachments and fails
        the same way. The fix is a Bluesky version of the post with at most
        ten slides, produced upstream.
        """
        if content.post_type == PostType.VIDEO:
            return
        attachments = content.media_files or content.media_urls or []
        if len(attachments) <= MAX_GALLERY_IMAGES:
            return
        raise PublishError(
            f"Bluesky zeigt höchstens {MAX_GALLERY_IMAGES} Bilder je Beitrag, dieser Beitrag "
            f"bringt {len(attachments)} mit. Es wird weder gekürzt noch auf mehrere Beiträge "
            f"verteilt: für Bluesky wird eine eigene, in sich geschlossene Fassung mit "
            f"höchstens {MAX_GALLERY_IMAGES} Folien gebraucht.",
            platform=self.platform_name,
            retryable=False,
        )

    def _create_post_record(
        self,
        access_token: str,
        *,
        did: str,
        text: str,
        embed: dict | None,
    ) -> dict:
        """Write a single app.bsky.feed.post record and return the API response."""
        record: dict = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

        # Parse facets (links, mentions, hashtags)
        facets = self._parse_facets(text, access_token)
        if facets:
            record["facets"] = facets

        if embed:
            record["embed"] = embed

        resp = self._request(
            "POST",
            f"{self.pds_url}/xrpc/com.atproto.repo.createRecord",
            access_token=access_token,
            json={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": record,
            },
        )
        return resp.json()

    # ------------------------------------------------------------------
    # Rich text facet parsing
    # ------------------------------------------------------------------

    def _parse_facets(self, text: str, access_token: str) -> list[dict]:
        """Parse links, mentions, and hashtags into Bluesky facet objects.

        Byte offsets are computed over the UTF-8 encoding of the text.
        """
        facets: list[dict] = []
        text.encode("utf-8")

        # Links
        link_pattern = re.compile(r"https?://[^\s\)\]>]+")
        for match in link_pattern.finditer(text):
            url = match.group(0)
            byte_start = len(text[: match.start()].encode("utf-8"))
            byte_end = len(text[: match.end()].encode("utf-8"))
            facets.append(
                {
                    "index": {"byteStart": byte_start, "byteEnd": byte_end},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
                }
            )

        # Mentions (@handle.bsky.social)
        mention_pattern = re.compile(r"(?<!\w)@([\w.-]+(?:\.[\w.-]+)+)")
        for match in mention_pattern.finditer(text):
            handle = match.group(1)
            byte_start = len(text[: match.start()].encode("utf-8"))
            byte_end = len(text[: match.end()].encode("utf-8"))
            try:
                did = self.resolve_handle(handle)
            except Exception:
                logger.warning("Could not resolve handle @%s, skipping facet", handle)
                continue
            facets.append(
                {
                    "index": {"byteStart": byte_start, "byteEnd": byte_end},
                    "features": [{"$type": "app.bsky.richtext.facet#mention", "did": did}],
                }
            )

        # Hashtags
        hashtag_pattern = re.compile(r"(?<!\w)#(\w+)")
        for match in hashtag_pattern.finditer(text):
            tag = match.group(1)
            byte_start = len(text[: match.start()].encode("utf-8"))
            byte_end = len(text[: match.end()].encode("utf-8"))
            facets.append(
                {
                    "index": {"byteStart": byte_start, "byteEnd": byte_end},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}],
                }
            )

        return facets

    # ------------------------------------------------------------------
    # Media helpers
    # ------------------------------------------------------------------

    def _upload_blob(self, access_token: str, media_path: str) -> dict:
        """Upload a blob to the PDS and return the blob reference."""
        import mimetypes

        mime_type, _ = mimetypes.guess_type(media_path)
        mime_type = mime_type or "application/octet-stream"

        with open(media_path, "rb") as f:
            file_bytes = f.read()

        resp = self._request(
            "POST",
            f"{self.pds_url}/xrpc/com.atproto.repo.uploadBlob",
            access_token=access_token,
            headers={"Content-Type": mime_type},
            data=file_bytes,
        )
        data = resp.json()
        return data.get("blob", data)

    @staticmethod
    def _aspect_ratio(media_path: str) -> dict:
        """Read the pixel dimensions of an image for the gallery embed.

        ``app.bsky.embed.gallery#image`` declares ``aspectRatio`` as REQUIRED
        (unlike ``images#image``, where it is optional), so a gallery post can
        only be built when the dimensions are known. Failing here beats posting
        a record the AppView will not accept.
        """
        try:
            from PIL import Image  # imported lazily: only gallery posts need it

            with Image.open(media_path) as img:
                width, height = img.size
        except Exception as exc:  # noqa: BLE001 - any failure means no dimensions
            raise PublishError(
                f"Bildmasse von {media_path} nicht lesbar, die Bluesky-Galerie verlangt sie "
                f"aber für jedes Bild: {exc}",
                platform="Bluesky",
                retryable=False,
            ) from exc
        if not width or not height:
            raise PublishError(
                f"Bild {media_path} meldet die Masse {width}x{height} - für die Bluesky-Galerie "
                "unbrauchbar",
                platform="Bluesky",
                retryable=False,
            )
        return {"width": width, "height": height}

    def _build_embed(self, access_token: str, content: PublishContent) -> dict | None:
        """Build the ONE embed of the post, uploading every blob up front.

        Returns ``None`` for a text-only post, a video embed for a video, an
        ``app.bsky.embed.images`` for up to four images and an
        ``app.bsky.embed.gallery`` beyond that. Four and fewer stay on the older
        embed on purpose: it is the form every client has understood for years,
        and the official app renders it exactly as before.
        """
        media_files = content.media_files or []
        if not media_files:
            return None

        if content.post_type == PostType.VIDEO:
            blob_ref = self._upload_blob(access_token, media_files[0])
            embed = {
                "$type": "app.bsky.embed.video",
                "video": blob_ref,
            }
            alt_text = content.alt_text_for(0, MAX_VIDEO_ALT_TEXT_LENGTH)
            if alt_text:
                embed["alt"] = alt_text
            return embed

        if content.post_type not in (PostType.IMAGE, PostType.CAROUSEL):
            return None

        as_gallery = len(media_files) > MAX_EMBED_IMAGES
        # Dimensions are read BEFORE the first upload: a gallery post that
        # cannot be completed should cost no blob at all.
        ratios = [self._aspect_ratio(path) for path in media_files] if as_gallery else []

        items: list[dict] = []
        for index, path in enumerate(media_files):
            blob_ref = self._upload_blob(access_token, path)
            # ``alt`` is required by both lexicons, so an attachment without alt
            # text still ships an empty string. The index keeps each description
            # on its own slide.
            item = {
                "alt": content.alt_text_for(index, MAX_ALT_TEXT_LENGTH),
                "image": blob_ref,
            }
            if as_gallery:
                item["aspectRatio"] = ratios[index]
            items.append(item)

        if as_gallery:
            return {"$type": "app.bsky.embed.gallery", "items": items}
        return {"$type": "app.bsky.embed.images", "images": items}
