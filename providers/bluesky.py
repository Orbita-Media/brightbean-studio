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

# app.bsky.embed.images allows at most 4 images per post. Anything beyond that
# is not dropped — it continues in a reply chain (see _build_embeds).
MAX_EMBED_IMAGES = 4

# Text of the continuation replies. Deliberately short: the 300 grapheme cap
# applies to every post in the chain, replies included. ``{start}``, ``{end}``
# and ``{total}`` are filled in; a caller-supplied override in
# ``extra["thread_continuation_text"]`` may use the same placeholders or none.
THREAD_CONTINUATION_TEXT = "Fortsetzung – Bild {start} bis {end} von {total}"
THREAD_CONTINUATION_TEXT_SINGLE = "Fortsetzung – Bild {start} von {total}"

# app.bsky.embed.images#image declares ``alt`` as required with no length
# constraint; we still bound it so one runaway description can't blow the
# record size. app.bsky.embed.video#main caps ``alt`` at 1000 graphemes —
# len() counts code points and a grapheme is never fewer than one code point,
# so truncating to 1000 characters always stays inside that limit.
MAX_ALT_TEXT_LENGTH = 2000
MAX_VIDEO_ALT_TEXT_LENGTH = 1000


def _access_jwt_expires_in(access_jwt: str) -> int | None:
    """Return seconds until an AT Protocol access JWT expires, or None if unknown.

    The createSession / refreshSession responses don't include an expiry field;
    the only source of truth is the JWT's own `exp` claim. We decode the payload
    without verifying the signature — we're reading metadata from a token the
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
        return [PostType.TEXT, PostType.IMAGE, PostType.VIDEO]

    @property
    def supported_media_types(self) -> list[MediaType]:
        return [MediaType.JPEG, MediaType.PNG, MediaType.MP4]

    @property
    def max_media_per_post(self) -> int | None:
        return MAX_EMBED_IMAGES

    # Overflow images continue as replies to our own post, so nothing is lost.
    chains_overflow_media = True

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
        """Publish a post to Bluesky via com.atproto.repo.createRecord.

        A carousel with more than ``MAX_EMBED_IMAGES`` slides becomes a reply
        chain: the first four images ride the root post, every further group of
        four follows as a reply to the previous post. Reply chains are ordinary
        reading on Bluesky, and the alternative — cutting the carousel off at
        four — throws away the closing slide.
        """
        # Validate grapheme length
        grapheme_count = len(content.text) if content.text else 0
        if grapheme_count > self.max_caption_length:
            raise PublishError(
                f"Post text exceeds {self.max_caption_length} graphemes (got {grapheme_count})",
                platform=self.platform_name,
            )

        # Get session DID
        session = self._request(
            "GET",
            f"{self.pds_url}/xrpc/com.atproto.server.getSession",
            access_token=access_token,
        ).json()
        did = session["did"]
        handle = session.get("handle", "")

        # Upload every blob BEFORE the first record is created. Uploading is by
        # far the likeliest step to fail (size, network, PDS hiccup) and while
        # nothing is posted yet a failure costs nothing — the publisher just
        # retries. Once the root post exists, a retry would post the whole
        # carousel a second time, so from here on we must not raise.
        embeds = self._build_embeds(access_token, content)
        # A text-only post still needs exactly one record.
        embeds = embeds or [None]
        total_images = sum(len(e.get("images", [])) for e in embeds if e)

        root_ref: dict | None = None
        parent_ref: dict | None = None
        first_data: dict = {}
        chain: list[dict] = []
        images_before = 0
        warning = ""

        for index, embed in enumerate(embeds):
            image_count = len(embed.get("images", [])) if embed else 0
            if index == 0:
                text = content.text or ""
            else:
                text = self._continuation_text(
                    content,
                    start=images_before + 1,
                    end=images_before + image_count,
                    total=total_images,
                )

            try:
                data = self._create_post_record(
                    access_token,
                    did=did,
                    text=text,
                    embed=embed,
                    root_ref=root_ref,
                    parent_ref=parent_ref,
                )
            except Exception as exc:
                if index == 0:
                    raise
                # The root is already live. Raising here would hand the
                # publisher a retryable failure and it would post the whole
                # carousel again, so we report the gap instead.
                warning = (
                    f"Bluesky-Kette unvollständig: {len(chain)} von {len(embeds)} Beiträgen "
                    f"veröffentlicht, ab Bild {images_before + 1} fehlt der Rest ({exc})"
                )
                logger.error(warning)
                break

            ref = {"uri": data.get("uri", ""), "cid": data.get("cid", "")}
            if index == 0:
                root_ref = ref
                first_data = data
            parent_ref = ref
            chain.append({**ref, "image_count": image_count})
            images_before += image_count

        # Build the web URL from the handle and rkey of the ROOT post — that is
        # the entry point of the thread and the id analytics/inbox work with.
        uri = first_data.get("uri", "")
        rkey = uri.split("/")[-1] if uri else ""
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None

        extra = dict(first_data)
        if len(embeds) > 1:
            extra["thread"] = {
                "posts": chain,
                "expected_posts": len(embeds),
                "image_count": images_before,
                "expected_image_count": total_images,
                "incomplete": len(chain) < len(embeds),
            }
        if warning:
            extra["publish_warning"] = warning

        return PublishResult(
            platform_post_id=uri,
            url=post_url,
            extra=extra,
        )

    def _create_post_record(
        self,
        access_token: str,
        *,
        did: str,
        text: str,
        embed: dict | None,
        root_ref: dict | None,
        parent_ref: dict | None,
    ) -> dict:
        """Write a single app.bsky.feed.post record and return the API response.

        ``root_ref`` / ``parent_ref`` turn the record into a reply. Both are
        required by the lexicon — a reply that names only the parent is not
        threaded by the AppView and ends up hanging in nowhere.
        """
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

        if root_ref and parent_ref:
            record["reply"] = {"root": root_ref, "parent": parent_ref}

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

    def _continuation_text(self, content: PublishContent, *, start: int, end: int, total: int) -> str:
        """Build the short text of a continuation reply.

        Callers may override the wording via ``extra["thread_continuation_text"]``;
        the placeholders are optional there. Whatever comes out is capped at the
        platform limit, because a reply that is too long fails the same way a
        post does.
        """
        template = str(content.extra.get("thread_continuation_text") or "").strip()
        if not template:
            template = THREAD_CONTINUATION_TEXT_SINGLE if start == end else THREAD_CONTINUATION_TEXT
        try:
            text = template.format(start=start, end=end, total=total)
        except (IndexError, KeyError):
            # A custom template with unknown placeholders is used verbatim
            # rather than failing the publish over a formatting detail.
            text = template
        return text[: self.max_caption_length]

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

    def _build_embeds(self, access_token: str, content: PublishContent) -> list[dict]:
        """Build one embed per post of the chain, uploading every blob up front.

        Returns an empty list for a text-only post, a single-element list for a
        video or for up to ``MAX_EMBED_IMAGES`` images, and one further element
        per group of four overflowing images.
        """
        media_files = content.media_files or []
        if not media_files:
            return []

        if content.post_type == PostType.VIDEO:
            blob_ref = self._upload_blob(access_token, media_files[0])
            embed = {
                "$type": "app.bsky.embed.video",
                "video": blob_ref,
            }
            alt_text = content.alt_text_for(0, MAX_VIDEO_ALT_TEXT_LENGTH)
            if alt_text:
                embed["alt"] = alt_text
            return [embed]

        if content.post_type == PostType.IMAGE:
            embeds: list[dict] = []
            images: list[dict] = []
            # ``index`` is the position in the ORIGINAL attachment list and the
            # only thing alt_text_for() is ever asked for. Chunking must not
            # restart it, otherwise slide 5 would inherit the description of
            # slide 1 — a wrong alt text is worse than a missing one.
            for index, path in enumerate(media_files):
                blob_ref = self._upload_blob(access_token, path)
                # ``alt`` is required by the lexicon, so an attachment without
                # alt text still ships an empty string. The index keeps each
                # description on its own slide.
                images.append(
                    {
                        "alt": content.alt_text_for(index, MAX_ALT_TEXT_LENGTH),
                        "image": blob_ref,
                    }
                )
                if len(images) == MAX_EMBED_IMAGES:
                    embeds.append({"$type": "app.bsky.embed.images", "images": images})
                    images = []
            if images:
                embeds.append({"$type": "app.bsky.embed.images", "images": images})
            return embeds

        return []
