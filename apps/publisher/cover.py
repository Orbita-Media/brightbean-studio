"""Titelbild (cover) of a video post – before and after publishing.

Used by ``POST /api/v1/posts/{id}/cover`` and by the ``platform_overrides``
of ``POST``/``PATCH /api/v1/posts``. The platform rules themselves live in
``providers/video_cover.py``; this module decides, per ``PlatformPost``, what
a change means in its current state:

* not published yet (draft, scheduled, in review, failed …): the value goes
  into ``platform_extra`` and is used at publish time. Date and status stay
  untouched – all 499 posts of the content plan are already ``scheduled``.
* published: YouTube (thumbnails.set) and Facebook (``/{video_id}/thumbnails``)
  change the live video. Instagram and TikTok cannot change a cover after
  publishing – their APIs fix it at creation time – and say so.
* publishing right now: refused by the caller (409), a change in that window
  would race the publisher.

Every call is idempotent: the same request twice changes nothing the second
time and reports ``unchanged``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass

from providers.video_cover import (
    COVER_IMAGE_PLATFORMS,
    COVER_OFFSET_PLATFORMS,
    EXTRA_COVER_ASSET,
    FACEBOOK_THUMBNAIL_MAX_BYTES,
    INSTAGRAM_COVER_MAX_BYTES,
    INSTAGRAM_COVER_MIME_TYPES,
    apply_cover_to_extra,
)

logger = logging.getLogger(__name__)

RESULT_SAVED = "saved"
RESULT_UPDATED = "updated"
RESULT_UNCHANGED = "unchanged"
RESULT_UNSUPPORTED = "unsupported"
RESULT_ERROR = "error"

#: YouTube: "Maximum file size: 2MB" (thumbnails.set).
YOUTUBE_THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024

#: Platforms that change the cover of an already published video.
LIVE_COVER_PLATFORMS = ("youtube", "facebook")

PLATFORM_LABELS = {
    "instagram": "Instagram",
    "instagram_login": "Instagram",
    "facebook": "Facebook",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "bluesky": "Bluesky",
    "linkedin_personal": "LinkedIn",
    "linkedin_company": "LinkedIn",
    "pinterest": "Pinterest",
    "threads": "Threads",
    "mastodon": "Mastodon",
    "google_business": "Google Business",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


@dataclass(frozen=True)
class CoverChange:
    """What the caller asked for. ``set_*`` False = field not sent, keep it."""

    set_asset: bool = False
    asset: object | None = None  # MediaAsset; None with set_asset = remove
    set_offset: bool = False
    offset_ms: int | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.set_asset or self.set_offset)

    def for_platform(self, platform: str) -> CoverChange:
        """Only the parts this platform understands."""
        return CoverChange(
            set_asset=self.set_asset and platform in COVER_IMAGE_PLATFORMS,
            asset=self.asset if platform in COVER_IMAGE_PLATFORMS else None,
            set_offset=self.set_offset and platform in COVER_OFFSET_PLATFORMS,
            offset_ms=self.offset_ms if platform in COVER_OFFSET_PLATFORMS else None,
        )


def unsupported_fields_message(platform: str, change: CoverChange) -> str | None:
    """German text naming the sent fields this platform cannot use, or None."""
    label = platform_label(platform)
    problems = []
    if change.set_asset and platform not in COVER_IMAGE_PLATFORMS:
        if platform in COVER_OFFSET_PLATFORMS:
            problems.append(f"{label} nimmt als Titelbild nur einen Frame aus dem Video (cover_offset_ms), kein Bild")
        else:
            problems.append(f"{label} kennt über die Schnittstelle kein Titelbild für Videos")
    if change.set_offset and platform not in COVER_OFFSET_PLATFORMS:
        if platform in COVER_IMAGE_PLATFORMS:
            problems.append(f"{label} nimmt als Titelbild nur ein Bild (cover_asset_id), keinen Frame")
        elif not problems:
            problems.append(f"{label} kennt über die Schnittstelle kein Titelbild für Videos")
    return "; ".join(problems) + "." if problems else None


def asset_problem(platform: str, asset) -> str | None:
    """Why this image cannot be the cover on *platform* (German), or None."""
    if asset is None:
        return None
    size = getattr(asset, "file_size", 0) or 0
    mime = (getattr(asset, "mime_type", "") or "").lower()
    name = (getattr(asset, "filename", "") or "").lower()
    if platform in ("instagram", "instagram_login"):
        is_jpeg = mime in INSTAGRAM_COVER_MIME_TYPES or (not mime and name.endswith((".jpg", ".jpeg")))
        if not is_jpeg:
            return f"Instagram nimmt als Titelbild nur JPEG; das Bild ist {mime or name or 'kein JPEG'}."
        if size > INSTAGRAM_COVER_MAX_BYTES:
            return "Instagram nimmt als Titelbild höchstens 8 MB."
    if platform == "facebook" and size > FACEBOOK_THUMBNAIL_MAX_BYTES:
        return "Facebook nimmt als Titelbild höchstens 10 MB."
    if platform == "youtube" and size > YOUTUBE_THUMBNAIL_MAX_BYTES:
        return "YouTube nimmt als Titelbild höchstens 2 MB."
    return None


def offset_problem(offset_ms: int | None, videos: list) -> str | None:
    """The frame must lie inside the video, when its length is known."""
    if offset_ms is None:
        return None
    durations = [float(getattr(v, "duration", 0) or 0) for v in videos]
    known = [d for d in durations if d > 0]
    if known and offset_ms > int(max(known) * 1000):
        return f"cover_offset_ms {offset_ms} liegt hinter dem Videoende ({int(max(known) * 1000)} ms)."
    return None


def _result(pp, result: str, message: str) -> dict:
    return {
        "social_account_id": pp.social_account_id,
        "platform": pp.social_account.platform,
        "status": pp.status,
        "result": result,
        "message": message,
    }


def apply_cover(pp, change: CoverChange) -> dict:
    """Apply *change* to one PlatformPost and report what happened.

    The caller has checked workspace, permissions, the asset (image of this
    workspace) and that nothing is being published right now.
    """
    from apps.composer.models import PlatformPost

    platform = pp.social_account.platform
    label = platform_label(platform)
    own = change.for_platform(platform)
    if own.is_empty:
        return _result(
            pp,
            RESULT_UNSUPPORTED,
            unsupported_fields_message(platform, change) or f"{label}: nichts zu tun.",
        )
    skipped = unsupported_fields_message(platform, change)
    note = f" Nicht übernommen: {skipped}" if skipped else ""

    from providers.story import is_story

    wants_value = (own.set_asset and own.asset is not None) or (own.set_offset and own.offset_ms is not None)
    if wants_value and is_story(pp.platform_extra):
        # Removing a leftover value is fine; setting one would be ignored at
        # publish time, so it is not stored in the first place.
        return _result(
            pp,
            RESULT_UNSUPPORTED,
            f"{label}: Dieser Kanal wird als Story veröffentlicht, eine Story hat kein Titelbild.",
        )

    if own.set_asset and own.asset is not None:
        problem = asset_problem(platform, own.asset)
        if problem:
            return _result(pp, RESULT_ERROR, problem)

    if pp.status != PlatformPost.Status.PUBLISHED:
        old = dict(pp.platform_extra or {})
        new = apply_cover_to_extra(
            old,
            platform,
            set_asset=own.set_asset,
            asset_id=own.asset.id if own.asset is not None else None,
            set_offset=own.set_offset,
            offset_ms=own.offset_ms,
        )
        if new == old:
            return _result(pp, RESULT_UNCHANGED, f"{label}: Titelbild war schon so gespeichert.{note}")
        pp.platform_extra = new
        pp.save(update_fields=["platform_extra", "updated_at"])
        return _result(
            pp,
            RESULT_SAVED,
            f"{label}: Titelbild gespeichert, es wird beim Veröffentlichen gesetzt. Termin und Status unverändert.{note}",
        )

    # ---- Already published.
    if platform not in LIVE_COVER_PLATFORMS:
        if platform in ("instagram", "instagram_login"):
            why = (
                "Instagram: Das Titelbild eines veröffentlichten Reels ist per API nach dem Veröffentlichen "
                "nicht änderbar (die Graph-API erlaubt dort nur comment_enabled). Nur in der Instagram-App."
            )
        elif platform == "tiktok":
            why = "TikTok: Das Titelbild ist per API nach dem Veröffentlichen nicht änderbar. Nur in der TikTok-App."
        else:
            why = f"{label}: Das Titelbild ist per API nach dem Veröffentlichen nicht änderbar."
        return _result(pp, RESULT_UNSUPPORTED, why)

    if not own.set_asset:
        return _result(pp, RESULT_UNSUPPORTED, f"{label}: nimmt nach dem Veröffentlichen nur ein Bild.{note}")
    if own.asset is None:
        return _result(
            pp,
            RESULT_UNSUPPORTED,
            f"{label}: Ein gesetztes Titelbild lässt sich per API nicht entfernen, nur durch ein anderes Bild ersetzen.",
        )

    extra = dict(pp.platform_extra or {})
    if str(extra.get(EXTRA_COVER_ASSET) or "") == str(own.asset.id) and extra.get("thumbnail_set") is True:
        return _result(pp, RESULT_UNCHANGED, f"{label}: Dieses Titelbild ist schon gesetzt.")

    video_id = (
        str(extra.get("video_id") or pp.platform_post_id or "") if platform == "facebook" else pp.platform_post_id
    )
    if not video_id:
        return _result(
            pp, RESULT_ERROR, f"{label}: Keine Video-ID gespeichert, das Titelbild lässt sich nicht zuordnen."
        )

    try:
        _set_live_thumbnail(pp.social_account, video_id, own.asset)
    except Exception as exc:  # noqa: BLE001 - every provider/storage failure becomes a per-channel error
        logger.warning("Cover for published %s video %s not set: %s", platform, video_id, exc)
        return _result(pp, RESULT_ERROR, f"{label}: Titelbild nicht gesetzt – {str(exc)[:300]}")

    extra[EXTRA_COVER_ASSET] = str(own.asset.id)
    extra["thumbnail_set"] = True
    extra.pop("thumbnail_error", None)
    pp.platform_extra = extra
    pp.save(update_fields=["platform_extra", "updated_at"])
    return _result(pp, RESULT_UPDATED, f"{label}: Titelbild am veröffentlichten Video geändert.")


def _set_live_thumbnail(account, video_id: str, asset) -> None:
    """Download the image to a temp file and hand it to the provider."""
    from apps.publisher.engine import _resolve_publish_credentials
    from providers import get_provider

    provider = get_provider(account.platform, _resolve_publish_credentials(account))
    access_token = account.oauth_access_token
    if account.token_expires_at and account.is_token_expiring_soon and account.oauth_refresh_token:
        try:
            access_token = account.refresh_oauth_token(provider)
        except Exception:
            logger.exception("Token refresh failed for %s", account)

    suffix = os.path.splitext(asset.filename or "")[1] or ".jpg"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)  # noqa: SIM115
    try:
        with asset.file.open("rb") as src:
            for chunk in iter(lambda: src.read(8192), b""):
                tmp.write(chunk)
        tmp.close()
        provider.set_video_thumbnail(access_token, video_id, tmp.name)
    finally:
        tmp.close()
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)
