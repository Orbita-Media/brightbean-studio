"""Storys auf Instagram und Facebook-Seiten – Regeln, die überall gelten.

Anlass (24.09.2026): Jeder Feed-Beitrag und jeder Reel soll rund 26 Stunden
nach der Veröffentlichung zusätzlich als Story erscheinen, damit das Konto
dauerhaft eine Story zeigt. Das Content-Tool plant diese Storys selbst als
eigene Beiträge mit Termin ein; der Verteiler muss dafür nur den Beitragstyp
„Story“ können.

Gespeichert wird der Typ als Hinweis ``platform_extra["post_type"] = "story"``
am ``PlatformPost``. Die Publish-Engine liest ihn in ``_resolve_post_type``
und übergibt ``PostType.STORY`` an den Provider.

Grenzen laut offizieller Doku (geprüft 24.09.2026):

* Instagram, ``POST /{ig-user-id}/media`` mit ``media_type=STORIES`` (gleich
  für ``/me/media`` beim Instagram-Login): Bild JPEG, höchstens 8 MB, 9:16
  empfohlen. Video MOV oder MP4, höchstens 100 MB, **3 bis 60 Sekunden**,
  23–60 fps, höchstens 1920 px breit, 9:16 empfohlen. ``caption``,
  ``collaborators``, ``alt_text``, ``location_id`` und ``cover_url`` gibt es
  bei Storys nicht; Sticker (Link, Umfrage, Ort) lassen sich per API nicht
  veröffentlichen.
* Facebook-Seite, Page Stories API: Foto (JPEG, BMP, PNG, GIF, TIFF, höchstens
  10 MB) wird unveröffentlicht über ``/{page_id}/photos`` hochgeladen und dann
  mit ``/{page_id}/photo_stories`` als Story veröffentlicht. Video (MP4
  empfohlen, 9:16, mindestens 540 × 960, 24–60 fps) läuft über
  ``/{page_id}/video_stories`` in drei Schritten (start, Upload, finish); eine
  Video-Story darf **höchstens 60 Sekunden** lang sein, mindestens 3.
  Ein Foto oder Video, das schon in einem veröffentlichten Beitrag steckt,
  lehnt Facebook für eine Story ab – das Upload geschieht deshalb immer neu.

Diese Datei kennt keine Django-Modelle: Medien werden per Attribut gelesen
(``media_type``, ``mime_type``, ``file_size``, ``duration``, ``filename``),
damit API, Composer und Provider dieselben Prüfungen benutzen.
"""

from __future__ import annotations

from urllib.parse import urlparse

#: Key and value in ``PlatformPost.platform_extra``.
EXTRA_POST_TYPE = "post_type"
STORY = "story"

#: Channels that publish a story through the API.
STORY_PLATFORMS = ("instagram", "instagram_login", "facebook")

#: Both platforms: "3 seconds minimum", "60 seconds maximum" (Instagram);
#: "3 to 90 seconds", stories "can not exceed 60 seconds" (Facebook).
STORY_MIN_SECONDS = 3
STORY_MAX_SECONDS = 60

INSTAGRAM_STORY_IMAGE_MAX_BYTES = 8 * 1024 * 1024
INSTAGRAM_STORY_VIDEO_MAX_BYTES = 100 * 1024 * 1024
INSTAGRAM_STORY_IMAGE_MIME_TYPES = ("image/jpeg", "image/jpg", "image/pjpeg")
INSTAGRAM_STORY_VIDEO_MIME_TYPES = ("video/mp4", "video/quicktime")

FACEBOOK_STORY_IMAGE_MAX_BYTES = 10 * 1024 * 1024
FACEBOOK_STORY_IMAGE_MIME_TYPES = (
    "image/jpeg",
    "image/jpg",
    "image/pjpeg",
    "image/png",
    "image/gif",
    "image/bmp",
    "image/tiff",
)
FACEBOOK_STORY_VIDEO_MIME_TYPES = ("video/mp4", "video/quicktime")

#: Extension heuristic for a video URL when the media type is unknown.
VIDEO_URL_SUFFIXES = (".mp4", ".mov", ".m4v")

PLATFORM_LABELS = {"instagram": "Instagram", "instagram_login": "Instagram", "facebook": "Facebook"}


def is_story(extra: dict | None) -> bool:
    """Whether ``platform_extra`` marks this channel's post as a story."""
    return str((extra or {}).get(EXTRA_POST_TYPE) or "").strip().lower() == STORY


def apply_story_to_extra(extra: dict | None, story: bool) -> dict:
    """Return a copy of *extra* with the story hint set or removed.

    Every other key (sound, collaborators, trial, cover …) survives. Switching
    the story off only removes our own value: a different hint someone stored
    by hand (``"reel"``) is not ours to delete.
    """
    new = dict(extra or {})
    if story:
        new[EXTRA_POST_TYPE] = STORY
    elif is_story(new):
        new.pop(EXTRA_POST_TYPE, None)
    return new


def is_video_url(url: str) -> bool:
    """Detect a video by the URL path, so a presigned query string can't hide it."""
    return urlparse(url or "").path.lower().endswith(VIDEO_URL_SUFFIXES)


def _label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def _is_video_asset(asset) -> bool:
    kind = str(getattr(asset, "media_type", "") or "").lower()
    if kind:
        return kind == "video"
    return bool(getattr(asset, "is_video", False))


def story_duration_problem(platform: str, seconds: float | None) -> str | None:
    """German text when a story video is too short or too long, else None.

    An unknown length (0 or None, e.g. still being processed) passes; the
    provider checks again with the real value right before publishing.
    """
    try:
        value = float(seconds or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    label = _label(platform)
    if value > STORY_MAX_SECONDS:
        return (
            f"{label} nimmt als Story-Video höchstens {STORY_MAX_SECONDS} Sekunden; "
            f"dieses Video ist {value:.1f} Sekunden lang. Kürzen oder als Reel/Video veröffentlichen."
        )
    if value < STORY_MIN_SECONDS:
        return (
            f"{label} nimmt als Story-Video mindestens {STORY_MIN_SECONDS} Sekunden; "
            f"dieses Video ist {value:.1f} Sekunden lang."
        )
    return None


def story_media_problem(platform: str, assets: list) -> str | None:
    """Why these media cannot be a story on *platform* (German), or None.

    A story is exactly one image or one video. No media, a carousel or a
    document are refused, and so is a file the platform does not take.
    """
    label = _label(platform)
    if platform not in STORY_PLATFORMS:
        return f"Storys gibt es über die Schnittstelle nur für Instagram und Facebook-Seiten, nicht für {platform}."
    if not assets:
        return f"Eine {label}-Story braucht genau ein Bild oder ein Video; dieser Beitrag hat kein Medium."
    if len(assets) != 1:
        return (
            f"Eine {label}-Story ist genau ein Bild oder ein Video; dieser Beitrag hat {len(assets)} Medien. "
            "Für ein Karussell je Folie eine eigene Story anlegen."
        )
    asset = assets[0]
    kind = str(getattr(asset, "media_type", "") or "").lower()
    mime = str(getattr(asset, "mime_type", "") or "").lower()
    name = str(getattr(asset, "filename", "") or "").lower()
    size = int(getattr(asset, "file_size", 0) or 0)

    if _is_video_asset(asset):
        allowed = INSTAGRAM_STORY_VIDEO_MIME_TYPES if platform != "facebook" else FACEBOOK_STORY_VIDEO_MIME_TYPES
        if mime and mime not in allowed:
            return f"{label} nimmt als Story-Video nur MP4 oder MOV; das Video ist {mime}."
        if platform != "facebook" and size > INSTAGRAM_STORY_VIDEO_MAX_BYTES:
            return f"Instagram nimmt als Story-Video höchstens 100 MB; das Video hat {size / 1024 / 1024:.0f} MB."
        return story_duration_problem(platform, getattr(asset, "duration", None))

    if kind and kind not in ("image", "gif"):
        return f"Eine {label}-Story ist ein Bild oder ein Video, kein {kind}."
    if platform == "facebook":
        if mime and mime not in FACEBOOK_STORY_IMAGE_MIME_TYPES:
            return f"Facebook nimmt als Story-Foto JPEG, PNG, GIF, BMP oder TIFF; das Bild ist {mime}."
        if size > FACEBOOK_STORY_IMAGE_MAX_BYTES:
            return f"Facebook nimmt als Story-Foto höchstens 10 MB; das Bild hat {size / 1024 / 1024:.1f} MB."
        return None
    is_jpeg = mime in INSTAGRAM_STORY_IMAGE_MIME_TYPES or (not mime and name.endswith((".jpg", ".jpeg")))
    if not is_jpeg:
        return f"Instagram nimmt als Story-Bild nur JPEG; das Bild ist {mime or name or 'kein JPEG'}."
    if size > INSTAGRAM_STORY_IMAGE_MAX_BYTES:
        return f"Instagram nimmt als Story-Bild höchstens 8 MB; das Bild hat {size / 1024 / 1024:.1f} MB."
    return None


def check_story_content(content, platform: str, platform_name: str) -> bool:
    """Last check right before a story goes out; returns whether it is a video.

    The API and the composer check the same rules earlier, but the length of
    a video is only known once the media library has processed it, and a
    post can be edited in between. Every failure here is permanent
    (``retryable=False``): the same file would fail the same way.
    """
    from .exceptions import PublishError

    count = len(content.media_urls or [])
    if count != 1:
        raise PublishError(
            f"Eine Story ist genau ein Bild oder ein Video; dieser Beitrag bringt {count} Medien mit.",
            platform=platform_name,
            retryable=False,
        )
    video = content.is_video_at(0)
    if video:
        problem = story_duration_problem(platform, content.video_duration_sec)
        if problem:
            raise PublishError(problem, platform=platform_name, retryable=False)
    return video


def instagram_story_fields(content, platform: str, platform_name: str) -> dict:
    """Container fields of an Instagram story (both connection types).

    Only ``media_type`` and the media URL: caption, alt text, collaborators,
    sound and cover do not exist on a story container and are left out.
    """
    video = check_story_content(content, platform, platform_name)
    url = content.media_urls[0]
    return {"media_type": "STORIES", "video_url" if video else "image_url": url}
