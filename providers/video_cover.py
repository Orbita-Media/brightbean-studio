"""Titelbild (cover / thumbnail) of a video post, shared by every channel.

Anlass (24.09.2026): The first reel went out with Instagram's default cover,
the first frame – a blurred background without the hook text. In the feed and
in the profile grid that frame is what people see before they tap, so each
reel gets its strongest image with the hook as cover.

Two ways to say which image, stored in ``PlatformPost.platform_extra``:

``thumbnail_asset_id``
    UUID of an image ``MediaAsset``. YouTube (thumbnails.set), Instagram
    (``cover_url``) and Facebook (``/{video_id}/thumbnails``).

``thumb_offset_ms``
    A frame of the video itself, in milliseconds. Instagram (``thumb_offset``)
    and TikTok (``video_cover_timestamp_ms``; the TikTok composer has always
    stored that key, so the API writes both for TikTok).

What each platform can do, from the official references (checked 24.09.2026):

* Instagram, ``POST /{ig-user-id}/media`` (and ``/me/media`` on the
  Instagram-Login path): ``cover_url`` – "For Reels only. The path to an image
  to use as the cover image for the Reels tab. We will cURL the image using the
  URL that you specify so the image must be on a public server." Spec: JPEG,
  at most 8 MB, sRGB, 9:16 recommended. ``thumb_offset`` – "For videos and
  reels. Location, in milliseconds, of the video or reel frame to be used as
  the cover thumbnail image. The default value is 0". "If you specify both
  ``cover_url`` and ``thumb_offset``, we use ``cover_url`` and ignore
  ``thumb_offset``." A published IG Media can only change
  ``comment_enabled`` – the cover is fixed once the reel is live.
* Facebook, ``POST /{video_id}/thumbnails``: ``source`` (image file, at most
  10 MB) and ``is_preferred``. Works on a published page video as well.
* TikTok, ``post_info.video_cover_timestamp_ms``: a frame by timestamp only,
  no image upload and no change after publishing.
* YouTube, ``thumbnails.set``: an image, before or after publishing.

The profile grid on Instagram (and TikTok) shows a 3:4 cut from the middle of
the 9:16 cover: the top and bottom eighth are lost. The hook belongs in the
middle 75 % of the image (docs/REEL-TITELBILD.md).
"""

from __future__ import annotations

import logging

from .types import PostType

logger = logging.getLogger(__name__)

#: Keys in ``PlatformPost.platform_extra``.
EXTRA_COVER_ASSET = "thumbnail_asset_id"
EXTRA_COVER_OFFSET = "thumb_offset_ms"
#: The TikTok provider (and the TikTok composer panel) read this one.
EXTRA_TIKTOK_COVER = "video_cover_timestamp_ms"

#: Filled in by the publish engine (never stored): the public absolute URL
#: of the cover image, and a local temp copy of it for upload-style APIs.
EXTRA_COVER_URL = "thumbnail_url"
EXTRA_COVER_FILE = "thumbnail_file"

#: Platforms that take a cover IMAGE.
COVER_IMAGE_PLATFORMS = ("youtube", "instagram", "instagram_login", "facebook", "pinterest")
#: Platforms that take a cover FRAME (offset into the video). Pinterest takes
#: whole seconds (``cover_image_key_frame_time``); the milliseconds are cut.
COVER_OFFSET_PLATFORMS = ("instagram", "instagram_login", "tiktok", "pinterest")
#: Pinterest video pins: the image lives under the composer's own key.
EXTRA_PINTEREST_COVER = "cover_image_asset_id"
#: Pinterest: ``cover_image_content_type`` enum is image/jpeg and image/png.
PINTEREST_COVER_MIME_TYPES = ("image/jpeg", "image/jpg", "image/pjpeg", "image/png")
#: Platforms where the public URL of the image is needed (Instagram fetches it).
COVER_URL_PLATFORMS = ("instagram", "instagram_login")

#: Instagram's cover spec: JPEG, at most 8 MB.
INSTAGRAM_COVER_MIME_TYPES = ("image/jpeg", "image/jpg", "image/pjpeg")
INSTAGRAM_COVER_MAX_BYTES = 8 * 1024 * 1024
#: Facebook: "Maximum thumbnail file size 10MB".
FACEBOOK_THUMBNAIL_MAX_BYTES = 10 * 1024 * 1024

#: Post types that go out as a REELS container on Instagram.
REEL_POST_TYPES = (PostType.REEL, PostType.VIDEO)


def parse_offset_ms(value) -> int | None:
    """``thumb_offset_ms`` as a non-negative int, or None.

    ``int()`` instead of ``str.isdigit()``: isdigit() accepts "²" or "١٢",
    which int() then rejects. Booleans are refused – ``True`` is an int in
    Python and would quietly become the frame at 1 ms.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip()) if not isinstance(value, int) else value
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def cover_offset_from_extra(extra: dict | None) -> int | None:
    """The stored frame offset, whichever of the two keys holds it."""
    extra = extra or {}
    offset = parse_offset_ms(extra.get(EXTRA_COVER_OFFSET))
    if offset is None:
        offset = parse_offset_ms(extra.get(EXTRA_TIKTOK_COVER))
    return offset


def apply_cover_to_extra(
    extra: dict | None,
    platform: str,
    *,
    set_asset: bool = False,
    asset_id=None,
    set_offset: bool = False,
    offset_ms: int | None = None,
) -> dict:
    """Write (or remove) the cover keys of one channel into a copy of *extra*.

    ``set_asset`` / ``set_offset`` say whether the caller touched that value at
    all; ``None`` with the flag set removes it. Everything else in *extra*
    (sound, collaborators, trial, privacy …) is left alone. TikTok gets the
    offset under both keys so its provider and composer panel keep working.
    """
    extra = dict(extra or {})
    asset_key = EXTRA_PINTEREST_COVER if platform == "pinterest" else EXTRA_COVER_ASSET
    if set_asset:
        if asset_id:
            extra[asset_key] = str(asset_id)
        else:
            extra.pop(asset_key, None)
    if set_offset:
        if offset_ms is None:
            extra.pop(EXTRA_COVER_OFFSET, None)
            if platform == "tiktok":
                extra.pop(EXTRA_TIKTOK_COVER, None)
        else:
            extra[EXTRA_COVER_OFFSET] = int(offset_ms)
            if platform == "tiktok":
                extra[EXTRA_TIKTOK_COVER] = int(offset_ms)
    return extra


def instagram_cover_fields(extra: dict | None, post_type: PostType) -> dict:
    """Container fields for the reel cover: ``cover_url`` or ``thumb_offset``.

    Only a REELS container takes them. On a story, an image or a carousel the
    setting is dropped with a warning instead of failing the post – a cover
    is presentation, not a promise about the audience like a trial reel.
    """
    extra = extra or {}
    cover_url = str(extra.get(EXTRA_COVER_URL) or "").strip()
    offset = parse_offset_ms(extra.get(EXTRA_COVER_OFFSET))
    if not cover_url and offset is None:
        return {}
    if post_type not in REEL_POST_TYPES:
        logger.warning(
            "Instagram: ignoring the cover (cover_url=%s, thumb_offset=%s) on a %s post, "
            "Graph takes a cover on reels only",
            cover_url or "-",
            offset if offset is not None else "-",
            post_type.value,
        )
        return {}
    fields: dict = {}
    if cover_url:
        # Meta: with both set, cover_url wins and thumb_offset is ignored.
        # Sending only the winner keeps the request unambiguous, and the
        # offset stays available as the fallback if the image is refused.
        fields["cover_url"] = cover_url
    elif offset is not None:
        fields["thumb_offset"] = offset
    return fields


def create_with_cover_fallback(create, payload: dict, *, fallback_offset: int | None = None, platform: str = ""):
    """Create a container; if Graph refuses it with a ``cover_url``, retry without.

    A cover that Instagram cannot fetch or does not accept (not a JPEG, over
    8 MB, not public) must not take a finished reel down with it: the retry
    publishes the reel with the chosen frame (``thumb_offset``) or, without
    one, with Instagram's default. A container is only a staged upload, an
    abandoned one is never published, so the retry is safe.

    ``create`` takes the payload and returns whatever the caller needs.
    Returns ``(create_result, cover_dropped)``.
    """
    from .exceptions import APIError, PublishError

    try:
        return create(payload), False
    except (APIError, PublishError) as exc:
        if "cover_url" not in payload:
            raise
        logger.warning(
            "%s: container rejected with cover_url=%s, retrying without the cover image (%s)",
            platform or "Instagram",
            payload["cover_url"],
            exc,
        )
        retry_payload = {k: v for k, v in payload.items() if k != "cover_url"}
        if fallback_offset is not None:
            retry_payload["thumb_offset"] = fallback_offset
        return create(retry_payload), True
