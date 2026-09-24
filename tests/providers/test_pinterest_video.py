"""Pinterest: video pins and the full board list.

* a video is recognised by its media type – nothing set the old ``is_video``
  flag, so every video pin went out as ``image_url`` and failed,
* the file is POSTed as multipart to ``upload_url`` together with all
  ``upload_parameters`` and without our bearer token (it is S3, not Pinterest),
* the pin waits for ``GET /media/{id}`` = ``succeeded``; ``failed`` is a
  ``PublishError``,
* the cover is ``cover_image_url`` (Titelbild) or else
  ``cover_image_key_frame_time`` in whole seconds from ``thumb_offset_ms``,
* 4 s to 5 min, checked before any request,
* ``get_boards`` follows the ``bookmark`` cursor instead of stopping at 25.
"""

from unittest.mock import MagicMock

import pytest

from providers.exceptions import PublishError
from providers.pinterest import PinterestProvider
from providers.types import PostType, PublishContent

VIDEO_URL = "https://social-cdn.example.com/media_library/2026/09/pin.mp4"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _provider():
    return PinterestProvider({"client_id": "id", "client_secret": "secret"})


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "pin.mp4"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42video")
    return str(path)


def _content(video_file, *, duration=30.0, **extra):
    return PublishContent(
        text="Beschreibung",
        title="Titel",
        media_urls=[VIDEO_URL],
        media_files=[video_file],
        media_types=["video"],
        post_type=PostType.VIDEO,
        video_duration_sec=duration,
        extra={"board_id": "1082834372847314271", **extra},
    )


def _flow(provider, statuses=("processing", "succeeded")):
    provider._request = MagicMock(
        side_effect=[
            _resp(
                {
                    "media_id": "555",
                    "media_type": "video",
                    "upload_url": "https://pinterest-media-upload.s3-accelerate.amazonaws.com/",
                    "upload_parameters": {"key": "uploads/555", "policy": "p", "x-amz-signature": "sig"},
                }
            ),
            _resp({}),
            *[_resp({"media_id": "555", "status": s}) for s in statuses],
            _resp({"id": "pin-1"}),
        ]
    )


def test_video_pin_upload_poll_and_cover_image(video_file, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    provider = _provider()
    _flow(provider)
    result = provider.publish_post("tok", _content(video_file, cover_image_url="https://cdn.example.com/cover.jpg"))

    calls = provider._request.call_args_list
    assert calls[0].args == ("POST", "https://api.pinterest.com/v5/media")
    assert calls[0].kwargs["json"] == {"media_type": "video"}

    upload = calls[1]
    assert upload.args == ("POST", "https://pinterest-media-upload.s3-accelerate.amazonaws.com/")
    assert upload.kwargs["data"] == {"key": "uploads/555", "policy": "p", "x-amz-signature": "sig"}
    assert upload.kwargs["files"]["file"][1].startswith(b"\x00\x00\x00\x18ftyp")
    assert "access_token" not in upload.kwargs, "S3 must not receive our Pinterest token"

    assert calls[2].args == ("GET", "https://api.pinterest.com/v5/media/555")
    pin = calls[4]
    assert pin.args == ("POST", "https://api.pinterest.com/v5/pins")
    assert pin.kwargs["json"]["board_id"] == "1082834372847314271"
    assert pin.kwargs["json"]["media_source"] == {
        "source_type": "video_id",
        "media_id": "555",
        "cover_image_url": "https://cdn.example.com/cover.jpg",
    }
    assert result.platform_post_id == "pin-1"
    assert result.url == "https://www.pinterest.com/pin/pin-1/"


def test_without_cover_image_the_frame_is_used_in_whole_seconds(video_file, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    provider = _provider()
    _flow(provider, statuses=("succeeded",))
    provider.publish_post("tok", _content(video_file, thumb_offset_ms=2750))
    source = provider._request.call_args_list[-1].kwargs["json"]["media_source"]
    assert source == {"source_type": "video_id", "media_id": "555", "cover_image_key_frame_time": 2}


def test_video_is_recognised_without_the_old_flag(video_file):
    provider = _provider()
    _flow(provider, statuses=("succeeded",))
    provider.publish_post("tok", _content(video_file))
    assert provider._request.call_args_list[0].kwargs["json"] == {"media_type": "video"}


def test_failed_processing_is_a_publish_error(video_file):
    provider = _provider()
    _flow(provider, statuses=("failed",))
    with pytest.raises(PublishError, match="nicht verarbeiten"):
        provider.publish_post("tok", _content(video_file))
    assert not any(c.args[1].endswith("/pins") for c in provider._request.call_args_list)


@pytest.mark.parametrize("duration", [3.0, 301.0])
def test_video_outside_4_seconds_to_5_minutes_fails_first(video_file, duration):
    provider = _provider()
    provider._request = MagicMock()
    with pytest.raises(PublishError) as exc:
        provider.publish_post("tok", _content(video_file, duration=duration))
    assert exc.value.retryable is False
    provider._request.assert_not_called()


def test_missing_upload_url_is_a_publish_error(video_file):
    provider = _provider()
    provider._request = MagicMock(side_effect=[_resp({"media_id": "555"})])
    with pytest.raises(PublishError, match="upload_url"):
        provider.publish_post("tok", _content(video_file))


def test_image_pin_is_unchanged():
    provider = _provider()
    provider._request = MagicMock(side_effect=[_resp({"id": "pin-2"})])
    content = PublishContent(
        text="x",
        media_urls=["https://cdn.example.com/pin.jpg"],
        media_types=["image"],
        post_type=PostType.PIN,
        extra={"board_id": "1"},
    )
    provider.publish_post("tok", content)
    assert provider._request.call_args.kwargs["json"]["media_source"] == {
        "source_type": "image_url",
        "url": "https://cdn.example.com/pin.jpg",
    }


def test_get_boards_follows_the_bookmark():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"items": [{"id": "1", "name": "A"}], "bookmark": "next"}),
            _resp({"items": [{"id": "2", "name": "B"}], "bookmark": None}),
        ]
    )
    boards = provider.get_boards("tok")
    assert [b["id"] for b in boards] == ["1", "2"]
    first, second = provider._request.call_args_list
    assert first.kwargs["params"] == {"page_size": 250}
    assert second.kwargs["params"] == {"page_size": 250, "bookmark": "next"}
