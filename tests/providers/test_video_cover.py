"""Titelbild (cover) of a video post across the providers.

Pinned here:

* Instagram (both connection types): ``cover_url`` for an image, otherwise
  ``thumb_offset`` for a frame; with both, only ``cover_url`` goes out (Meta:
  "we use cover_url and ignore thumb_offset"); reels only – a cover on a story
  or an image is dropped with a warning, never failing the post,
* a cover image Graph refuses does not take the reel down: the container is
  retried without it (with the chosen frame when there is one) and the
  platform sound survives that retry,
* Facebook sets the thumbnail after the upload, best-effort: a failure there
  is logged and recorded but never fails the (already live) video,
* YouTube and Facebook can change the cover of a published video, Instagram
  and TikTok cannot (NotImplementedError from the base class),
* TikTok reads the channel-neutral ``thumb_offset_ms`` as well.
"""

import json
from unittest.mock import MagicMock

import pytest

from providers.exceptions import APIError
from providers.facebook import FacebookProvider
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.tiktok import TikTokProvider
from providers.types import PostType, PublishContent
from providers.video_cover import (
    apply_cover_to_extra,
    cover_offset_from_extra,
    instagram_cover_fields,
    parse_offset_ms,
)
from providers.youtube import YouTubeProvider

REEL_URL = "https://cdn.example.com/reel.mp4"
COVER_URL = "https://social.example.com/media/cover.jpg"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _graph():
    return InstagramProvider({"client_id": "id", "client_secret": "secret"})


def _login():
    return InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})


def _content(post_type=PostType.REEL, media_urls=None, **extra):
    return PublishContent(
        text="Caption",
        media_urls=media_urls or [REEL_URL],
        post_type=post_type,
        extra={"ig_user_id": "ig-1", **extra},
    )


def _publish_flow(provider):
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )


def _container_payload(provider, index=0):
    return provider._request.call_args_list[index].kwargs["json"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_parse_offset_ms_accepts_ints_and_digit_strings_only():
    assert parse_offset_ms(0) == 0
    assert parse_offset_ms(2500) == 2500
    assert parse_offset_ms("1200") == 1200
    assert parse_offset_ms(" 40 ") == 40
    for bad in (None, -1, "-5", "abc", "²", True, False, "", 1.5j):
        assert parse_offset_ms(bad) is None, bad


def test_offset_is_read_from_either_key():
    assert cover_offset_from_extra({"thumb_offset_ms": 900}) == 900
    assert cover_offset_from_extra({"video_cover_timestamp_ms": 700}) == 700
    assert cover_offset_from_extra({"thumb_offset_ms": 1, "video_cover_timestamp_ms": 2}) == 1
    assert cover_offset_from_extra({}) is None
    assert cover_offset_from_extra(None) is None


def test_apply_cover_keeps_every_other_key_and_writes_tiktok_twice():
    extra = {"audio_id": "a1", "collaborators": ["x"], "trial": True}
    out = apply_cover_to_extra(extra, "instagram", set_asset=True, asset_id="asset-1", set_offset=True, offset_ms=500)
    assert out == {**extra, "thumbnail_asset_id": "asset-1", "thumb_offset_ms": 500}
    assert extra == {"audio_id": "a1", "collaborators": ["x"], "trial": True}, "input must not be mutated"

    tiktok = apply_cover_to_extra({"privacy_level": "SELF_ONLY"}, "tiktok", set_offset=True, offset_ms=1500)
    assert tiktok == {"privacy_level": "SELF_ONLY", "thumb_offset_ms": 1500, "video_cover_timestamp_ms": 1500}

    cleared = apply_cover_to_extra(tiktok, "tiktok", set_offset=True, offset_ms=None)
    assert cleared == {"privacy_level": "SELF_ONLY"}

    untouched = apply_cover_to_extra(out, "instagram")
    assert untouched == out

    no_asset = apply_cover_to_extra(out, "instagram", set_asset=True, asset_id=None)
    assert "thumbnail_asset_id" not in no_asset
    assert no_asset["thumb_offset_ms"] == 500


def test_instagram_cover_fields_prefers_the_image():
    assert instagram_cover_fields({}, PostType.REEL) == {}
    assert instagram_cover_fields({"thumbnail_url": COVER_URL}, PostType.REEL) == {"cover_url": COVER_URL}
    assert instagram_cover_fields({"thumb_offset_ms": 1800}, PostType.VIDEO) == {"thumb_offset": 1800}
    both = {"thumbnail_url": COVER_URL, "thumb_offset_ms": 1800}
    assert instagram_cover_fields(both, PostType.REEL) == {"cover_url": COVER_URL}


@pytest.mark.parametrize("post_type", [PostType.STORY, PostType.IMAGE, PostType.CAROUSEL])
def test_instagram_cover_fields_ignored_off_reels_with_warning(post_type, caplog):
    with caplog.at_level("WARNING"):
        assert instagram_cover_fields({"thumbnail_url": COVER_URL}, post_type) == {}
    assert "reels only" in caplog.text


# ---------------------------------------------------------------------------
# Instagram, both connection types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("make", [_graph, _login])
def test_reel_with_cover_image_sends_cover_url(make):
    provider = make()
    _publish_flow(provider)

    result = provider.publish_post("tok", _content(thumbnail_url=COVER_URL, thumb_offset_ms=900))

    payload = _container_payload(provider)
    assert payload["media_type"] == "REELS"
    assert payload["cover_url"] == COVER_URL
    assert "thumb_offset" not in payload, "cover_url wins; the offset is only the fallback"
    assert "cover_dropped" not in (result.extra or {})


@pytest.mark.parametrize("make", [_graph, _login])
@pytest.mark.parametrize("post_type", [PostType.REEL, PostType.VIDEO])
def test_reel_with_frame_sends_thumb_offset(make, post_type):
    provider = make()
    _publish_flow(provider)

    provider.publish_post("tok", _content(post_type=post_type, thumb_offset_ms="2400"))

    payload = _container_payload(provider)
    assert payload["media_type"] == "REELS"
    assert payload["thumb_offset"] == 2400
    assert "cover_url" not in payload


@pytest.mark.parametrize("make", [_graph, _login])
def test_reel_without_cover_sends_neither_field(make):
    provider = make()
    _publish_flow(provider)

    provider.publish_post("tok", _content())

    payload = _container_payload(provider)
    assert "cover_url" not in payload
    assert "thumb_offset" not in payload


@pytest.mark.parametrize("make", [_graph, _login])
def test_story_ignores_cover_and_still_publishes(make, caplog):
    provider = make()
    _publish_flow(provider)

    with caplog.at_level("WARNING"):
        result = provider.publish_post(
            "tok", _content(post_type=PostType.STORY, thumbnail_url=COVER_URL, thumb_offset_ms=100)
        )

    payload = _container_payload(provider)
    assert payload["media_type"] == "STORIES"
    assert "cover_url" not in payload
    assert "thumb_offset" not in payload
    assert result.platform_post_id == "media-1"
    assert "reels only" in caplog.text


@pytest.mark.parametrize("make", [_graph, _login])
def test_image_post_ignores_cover(make):
    provider = make()
    _publish_flow(provider)

    provider.publish_post(
        "tok",
        _content(post_type=PostType.IMAGE, media_urls=["https://cdn.example.com/a.jpg"], thumbnail_url=COVER_URL),
    )

    payload = _container_payload(provider)
    assert payload["image_url"] == "https://cdn.example.com/a.jpg"
    assert "cover_url" not in payload


@pytest.mark.parametrize("make", [_graph, _login])
def test_refused_cover_image_falls_back_to_the_frame(make):
    provider = make()
    provider._request = MagicMock(
        side_effect=[
            APIError("Instagram API error 400: cover_url invalid image", status_code=400),
            _resp({"id": "container-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-2"}),
        ]
    )

    result = provider.publish_post("tok", _content(thumbnail_url=COVER_URL, thumb_offset_ms=3100))

    first, retry = _container_payload(provider, 0), _container_payload(provider, 1)
    assert first["cover_url"] == COVER_URL
    assert "cover_url" not in retry
    assert retry["thumb_offset"] == 3100
    assert retry["video_url"] == REEL_URL
    assert result.platform_post_id == "media-2"
    assert result.extra["cover_dropped"] is True


@pytest.mark.parametrize("make", [_graph, _login])
def test_refused_cover_without_frame_publishes_with_default_cover(make):
    provider = make()
    provider._request = MagicMock(
        side_effect=[
            APIError("bad cover", status_code=400),
            _resp({"id": "container-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-2"}),
        ]
    )

    result = provider.publish_post("tok", _content(thumbnail_url=COVER_URL))

    retry = _container_payload(provider, 1)
    assert "cover_url" not in retry
    assert "thumb_offset" not in retry
    assert result.extra["cover_dropped"] is True


@pytest.mark.parametrize("make", [_graph, _login])
def test_error_without_cover_is_not_retried(make):
    provider = make()
    provider._request = MagicMock(side_effect=[APIError("video broken", status_code=400)])

    with pytest.raises(APIError):
        provider.publish_post("tok", _content(thumb_offset_ms=100))
    assert provider._request.call_count == 1


def test_refused_cover_keeps_the_platform_sound():
    """Graph path: the sound fallback runs inside the cover fallback.

    1st try: sound + cover → refused; sound retry: cover without sound →
    refused; cover retry: sound without cover → accepted. The reel keeps its
    sound and loses only the cover that caused the trouble.
    """
    provider = _graph()
    provider._request = MagicMock(
        side_effect=[
            APIError("cover refused", status_code=400),
            APIError("cover refused", status_code=400),
            _resp({"id": "container-3"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-3"}),
        ]
    )

    result = provider.publish_post("tok", _content(thumbnail_url=COVER_URL, audio_id="aud-1"))

    third = _container_payload(provider, 2)
    assert "cover_url" not in third
    assert json.loads(third["audio_configuration"])["audio_id"] == "aud-1"
    assert result.extra["cover_dropped"] is True
    assert result.extra["audio_id"] == "aud-1"
    assert "audio_dropped" not in result.extra


def test_instagram_cannot_change_a_published_cover():
    for provider in (_graph(), _login()):
        with pytest.raises(NotImplementedError):
            provider.set_video_thumbnail("tok", "media-1", "/tmp/cover.jpg")


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------


def _fb():
    return FacebookProvider({"client_id": "id", "client_secret": "secret"})


def _fb_video(**extra):
    return PublishContent(
        text="Video caption",
        media_urls=["https://cdn.example.com/clip.mp4"],
        post_type=PostType.VIDEO,
        extra={"page_id": "page-1", **extra},
    )


def test_facebook_video_sets_the_thumbnail_after_upload(tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8jpeg")
    provider = _fb()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "video-1"}),
            _resp({"post_id": "page-1_post-1", "permalink_url": "https://www.facebook.com/v/1"}),
            _resp({"success": True}),
        ]
    )
    provider._safe_json = MagicMock(return_value={"success": True})

    result = provider.publish_post("page-token", _fb_video(thumbnail_file=str(cover)))

    thumb_call = provider._request.call_args_list[2]
    assert thumb_call.args == ("POST", "https://graph.facebook.com/v25.0/video-1/thumbnails")
    assert thumb_call.kwargs["data"] == {"is_preferred": "true"}
    name, body, mime = thumb_call.kwargs["files"]["source"]
    assert (name, body, mime) == ("cover.jpg", b"\xff\xd8jpeg", "image/jpeg")
    assert result.extra["thumbnail_set"] is True
    assert result.platform_post_id == "post-1"


def test_facebook_thumbnail_failure_never_fails_the_video(tmp_path, caplog):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    provider = _fb()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "video-1"}),
            _resp({"post_id": "page-1_post-1"}),
            APIError("Facebook API error 400: video not ready", status_code=400),
        ]
    )

    with caplog.at_level("WARNING"):
        result = provider.publish_post("page-token", _fb_video(thumbnail_file=str(cover)))

    assert result.platform_post_id == "post-1"
    assert result.extra["video_id"] == "video-1"
    assert result.extra["thumbnail_set"] is False
    assert "video not ready" in result.extra["thumbnail_error"]
    assert "thumbnail not set" in caplog.text


def test_facebook_thumbnail_missing_file_is_recorded_not_raised():
    provider = _fb()
    provider._request = MagicMock(side_effect=[_resp({"id": "video-1"}), _resp({})])

    result = provider.publish_post("page-token", _fb_video(thumbnail_file="/nonexistent/cover.jpg"))

    assert result.extra["thumbnail_set"] is False
    assert provider._request.call_count == 2


def test_facebook_video_without_cover_makes_no_thumbnail_call():
    provider = _fb()
    provider._request = MagicMock(side_effect=[_resp({"id": "video-1"}), _resp({})])

    result = provider.publish_post("page-token", _fb_video())

    assert provider._request.call_count == 2
    assert "thumbnail_set" not in result.extra


def test_facebook_set_video_thumbnail_raises_when_refused(tmp_path):
    cover = tmp_path / "c.jpg"
    cover.write_bytes(b"x")
    provider = _fb()
    provider._request = MagicMock(return_value=MagicMock())
    provider._safe_json = MagicMock(return_value={"success": False})

    with pytest.raises(APIError):
        provider.set_video_thumbnail("tok", "video-9", str(cover))


# ---------------------------------------------------------------------------
# YouTube, TikTok
# ---------------------------------------------------------------------------


def test_youtube_set_video_thumbnail_calls_thumbnails_set(tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png-bytes")
    provider = YouTubeProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(return_value=MagicMock())
    provider._safe_json = MagicMock(return_value={"kind": "youtube#thumbnailSetResponse"})

    body = provider.set_video_thumbnail("tok", "yt-video-1", str(cover))

    call = provider._request.call_args
    assert call.args == ("POST", "https://www.googleapis.com/upload/youtube/v3/thumbnails/set")
    assert call.kwargs["params"] == {"videoId": "yt-video-1", "uploadType": "media"}
    assert call.kwargs["headers"]["Content-Type"] == "image/png"
    assert call.kwargs["data"] == b"png-bytes"
    assert body["kind"] == "youtube#thumbnailSetResponse"


def _tiktok_post_info(**extra):
    provider = TikTokProvider({"client_key": "k", "client_secret": "s"})
    content = PublishContent(text="t", media_urls=[REEL_URL], post_type=PostType.VIDEO, extra=extra)
    return provider._build_post_info(content, "PUBLIC_TO_EVERYONE")


def test_tiktok_reads_the_neutral_offset_key():
    assert _tiktok_post_info(thumb_offset_ms=4200)["video_cover_timestamp_ms"] == 4200
    assert _tiktok_post_info(thumb_offset_ms="4200")["video_cover_timestamp_ms"] == 4200
    # The TikTok-specific key (composer panel) wins over the neutral one.
    assert _tiktok_post_info(thumb_offset_ms=1, video_cover_timestamp_ms=2)["video_cover_timestamp_ms"] == 2
    assert "video_cover_timestamp_ms" not in _tiktok_post_info(thumb_offset_ms=True)
    assert "video_cover_timestamp_ms" not in _tiktok_post_info(thumb_offset_ms=-3)
    assert "video_cover_timestamp_ms" not in _tiktok_post_info()


def test_tiktok_cannot_change_a_published_cover():
    with pytest.raises(NotImplementedError):
        TikTokProvider({"client_key": "k", "client_secret": "s"}).set_video_thumbnail("t", "v", "/tmp/x.jpg")
