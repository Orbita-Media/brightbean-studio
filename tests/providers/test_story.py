"""Storys auf Instagram (beide Anschlussarten) und Facebook-Seiten.

Pinned here:

* Instagram: ``media_type=STORIES`` with ``image_url`` or ``video_url`` – the
  type comes from the media library, not from the file name, so a presigned
  URL or ``.MP4`` in capitals cannot turn a video into an image; no caption,
  collaborators, sound or cover on the container; the container is awaited
  before ``media_publish`` (video stories are transcoded); the permalink is
  looked up best-effort after publishing; a trial on a story is refused,
* Facebook: photo story = unpublished photo + ``photo_stories``; video story
  = ``video_stories`` start → hosted upload (``file_url`` header, OAuth
  token) → status poll → finish; the result carries ``post_id`` and a URL;
  errors are ``PublishError``, the staged photo is removed again,
* both: exactly one medium, a video of 3 to 60 seconds – otherwise a
  permanent ``PublishError`` before the first request.
"""

from unittest.mock import MagicMock

import pytest

from providers.exceptions import APIError, PublishError
from providers.facebook import FacebookProvider
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.story import (
    STORY_MAX_SECONDS,
    apply_story_to_extra,
    is_story,
    is_video_url,
    story_duration_problem,
    story_media_problem,
)
from providers.types import PostType, PublishContent

IMAGE_URL = "https://social-cdn.example.com/media_library/2026/09/folie1.jpg"
VIDEO_URL = "https://social-cdn.example.com/media_library/2026/09/reel.mp4"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _story(url=IMAGE_URL, media_type="image", duration=None, text="Caption", **extra):
    return PublishContent(
        text=text,
        media_urls=[url] if url else [],
        media_types=[media_type] if url else [],
        post_type=PostType.STORY,
        video_duration_sec=duration,
        extra={"ig_user_id": "ig-1", "page_id": "page-1", **extra},
    )


# ---------------------------------------------------------------------------
# Helpers in providers/story.py
# ---------------------------------------------------------------------------


def test_story_hint_is_set_and_removed_without_touching_other_keys():
    extra = {"collaborators": ["a"], "trial": True}
    on = apply_story_to_extra(extra, True)
    assert on == {"collaborators": ["a"], "trial": True, "post_type": "story"}
    assert extra == {"collaborators": ["a"], "trial": True}, "input must not be mutated"
    assert is_story(on)
    off = apply_story_to_extra(on, False)
    assert off == {"collaborators": ["a"], "trial": True}
    # A hint that is not ours stays.
    assert apply_story_to_extra({"post_type": "reel"}, False) == {"post_type": "reel"}


def test_video_url_detection_ignores_query_strings_and_case():
    assert is_video_url("https://x/a.MP4?X-Amz-Signature=abc")
    assert is_video_url("https://x/a.mov")
    assert not is_video_url("https://x/a.jpg?v=video.mp4")


def test_duration_limits():
    assert story_duration_problem("instagram", 0) is None
    assert story_duration_problem("instagram", None) is None
    assert story_duration_problem("instagram", STORY_MAX_SECONDS) is None
    assert story_duration_problem("facebook", 3) is None
    assert "höchstens 60" in story_duration_problem("facebook", 60.5)
    assert "mindestens 3" in story_duration_problem("instagram", 2.9)


class _Asset:
    def __init__(self, media_type="image", mime="image/jpeg", size=100, duration=0.0, filename="a.jpg"):
        self.media_type = media_type
        self.mime_type = mime
        self.file_size = size
        self.duration = duration
        self.filename = filename


def test_media_problem_covers_count_type_and_size():
    assert story_media_problem("instagram", [_Asset()]) is None
    assert "kein Medium" in story_media_problem("instagram", [])
    assert "2 Medien" in story_media_problem("facebook", [_Asset(), _Asset()])
    assert "JPEG" in story_media_problem("instagram", [_Asset(mime="image/png")])
    assert story_media_problem("facebook", [_Asset(mime="image/png")]) is None
    assert "8 MB" in story_media_problem("instagram", [_Asset(size=9 * 1024 * 1024)])
    assert "10 MB" in story_media_problem("facebook", [_Asset(size=11 * 1024 * 1024)])
    video = _Asset(media_type="video", mime="video/mp4", duration=54)
    assert story_media_problem("instagram", [video]) is None
    assert "MP4 oder MOV" in story_media_problem(
        "instagram", [_Asset(media_type="video", mime="video/webm", duration=10)]
    )
    assert "kein document" in story_media_problem("facebook", [_Asset(media_type="document", mime="application/pdf")])
    assert "nur für Instagram" in story_media_problem("youtube", [video])


# ---------------------------------------------------------------------------
# Instagram (Facebook-Login and Instagram-Login)
# ---------------------------------------------------------------------------


def _graph():
    return InstagramProvider({"client_id": "id", "client_secret": "secret"})


def _login():
    return InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})


def _ig_flow(provider, *, statuses=("FINISHED",), permalink="https://www.instagram.com/stories/orbita/1/"):
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            *[_resp({"status_code": s}) for s in statuses],
            _resp({"id": "media-1"}),
            _resp({"permalink": permalink} if permalink else {}),
        ]
    )


@pytest.mark.parametrize("factory", [_graph, _login], ids=["graph", "login"])
class TestInstagramStory:
    def test_image_story(self, factory):
        provider = factory()
        _ig_flow(provider)
        result = provider.publish_post("tok", _story(collaborators=["partner"], audio_id="1", thumb_offset_ms=500))
        payload = provider._request.call_args_list[0].kwargs["json"]
        assert payload == {"media_type": "STORIES", "image_url": IMAGE_URL}
        assert provider._request.call_args_list[0].args[1].endswith("/media")
        assert provider._request.call_args_list[2].args[1].endswith("/media_publish")
        assert result.platform_post_id == "media-1"
        assert result.url == "https://www.instagram.com/stories/orbita/1/"
        assert result.extra.get("story") is True

    def test_video_story_waits_for_the_container(self, factory, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *_: None)
        provider = factory()
        _ig_flow(provider, statuses=("IN_PROGRESS", "IN_PROGRESS", "FINISHED"))
        provider.publish_post("tok", _story(VIDEO_URL, "video", duration=54))
        payload = provider._request.call_args_list[0].kwargs["json"]
        assert payload == {"media_type": "STORIES", "video_url": VIDEO_URL}
        polls = [c for c in provider._request.call_args_list if c.args[0] == "GET" and "status_code" in str(c)]
        assert len(polls) == 3
        publish = provider._request.call_args_list[4]
        assert publish.args[1].endswith("/media_publish")
        assert publish.kwargs["json"] == {"creation_id": "container-1"}

    def test_video_type_comes_from_the_library_not_the_file_name(self, factory):
        provider = factory()
        _ig_flow(provider)
        signed = "https://cdn.example.com/abc?X-Amz-Signature=1"
        provider.publish_post("tok", _story(signed, "video", duration=20))
        assert provider._request.call_args_list[0].kwargs["json"]["video_url"] == signed

    def test_upper_case_extension_without_type_is_still_a_video(self, factory):
        provider = factory()
        _ig_flow(provider)
        content = _story("https://cdn.example.com/REEL.MP4", "video", duration=20)
        content.media_types = []
        provider.publish_post("tok", content)
        assert "video_url" in provider._request.call_args_list[0].kwargs["json"]

    def test_container_error_is_a_publish_error(self, factory):
        provider = factory()
        provider._request = MagicMock(
            side_effect=[_resp({"id": "container-1"}), _resp({"status_code": "ERROR", "status": "bad video"})]
        )
        with pytest.raises(PublishError, match="bad video"):
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=20))

    def test_permalink_failure_keeps_the_published_story(self, factory):
        provider = factory()
        provider._request = MagicMock(
            side_effect=[
                _resp({"id": "container-1"}),
                _resp({"status_code": "FINISHED"}),
                _resp({"id": "media-1"}),
                APIError("boom"),
            ]
        )
        result = provider.publish_post("tok", _story())
        assert result.platform_post_id == "media-1"
        assert result.url

    @pytest.mark.parametrize("duration", [61, 2])
    def test_video_outside_3_to_60_seconds_fails_before_any_request(self, factory, duration):
        provider = factory()
        provider._request = MagicMock()
        with pytest.raises(PublishError) as exc:
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=duration))
        assert exc.value.retryable is False
        provider._request.assert_not_called()

    def test_two_media_fail_before_any_request(self, factory):
        provider = factory()
        provider._request = MagicMock()
        content = _story()
        content.media_urls = [IMAGE_URL, IMAGE_URL]
        content.media_types = ["image", "image"]
        with pytest.raises(PublishError, match="genau ein Bild"):
            provider.publish_post("tok", content)
        provider._request.assert_not_called()

    def test_trial_on_a_story_is_refused(self, factory):
        provider = factory()
        provider._request = MagicMock()
        with pytest.raises(PublishError):
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=20, trial=True))
        provider._request.assert_not_called()


# ---------------------------------------------------------------------------
# Facebook page stories
# ---------------------------------------------------------------------------


def _fb():
    return FacebookProvider({"client_id": "id", "client_secret": "secret"})


class TestFacebookPhotoStory:
    def test_unpublished_photo_then_photo_stories(self):
        provider = _fb()
        provider._request = MagicMock(
            side_effect=[
                _resp({"id": "photo-1"}),
                _resp({"success": True, "post_id": "story-9"}),
                _resp({"data": [{"post_id": "story-9", "url": "https://www.facebook.com/stories/page-1/9/"}]}),
            ]
        )
        result = provider.publish_post("tok", _story(text="wird nicht gesendet"))
        calls = provider._request.call_args_list
        assert calls[0].args == ("POST", "https://graph.facebook.com/v25.0/page-1/photos")
        assert calls[0].kwargs["json"] == {"url": IMAGE_URL, "published": False}
        assert calls[1].args == ("POST", "https://graph.facebook.com/v25.0/page-1/photo_stories")
        assert calls[1].kwargs["json"] == {"photo_id": "photo-1"}
        assert calls[2].args[1].endswith("/page-1/stories")
        assert result.platform_post_id == "story-9"
        assert result.url == "https://www.facebook.com/stories/page-1/9/"
        assert result.extra["story"] is True
        assert result.extra["media_type"] == "photo"

    def test_story_step_failure_removes_the_staged_photo(self):
        provider = _fb()
        provider._request = MagicMock(
            side_effect=[
                _resp({"id": "photo-1"}),
                APIError("Facebook API error 400: invalid photo", status_code=400),
                _resp({"success": True}),
            ]
        )
        with pytest.raises(APIError):
            provider.publish_post("tok", _story())
        last = provider._request.call_args_list[-1]
        assert last.args == ("DELETE", "https://graph.facebook.com/v25.0/photo-1")

    def test_success_false_is_a_publish_error(self):
        provider = _fb()
        provider._request = MagicMock(
            side_effect=[_resp({"id": "photo-1"}), _resp({"success": False}), _resp({"success": True})]
        )
        with pytest.raises(PublishError, match="Foto-Story"):
            provider.publish_post("tok", _story())

    def test_missing_photo_id_is_a_publish_error(self):
        provider = _fb()
        provider._request = MagicMock(side_effect=[_resp({"error": "x"})])
        with pytest.raises(PublishError, match="photo_id"):
            provider.publish_post("tok", _story())

    def test_url_lookup_failure_falls_back(self):
        provider = _fb()
        provider._request = MagicMock(
            side_effect=[_resp({"id": "photo-1"}), _resp({"success": True, "post_id": "story-9"}), APIError("x")]
        )
        result = provider.publish_post("tok", _story())
        assert result.platform_post_id == "story-9"
        assert result.url == "https://www.facebook.com/stories/page-1/"


class TestFacebookVideoStory:
    def _flow(self, provider, statuses):
        provider._request = MagicMock(
            side_effect=[
                _resp({"video_id": "vid-1", "upload_url": "https://rupload.facebook.com/video-upload/v25.0/vid-1"}),
                MagicMock(json=MagicMock(return_value={"success": True})),
                *[_resp({"status": s}) for s in statuses],
                _resp({"success": True, "post_id": "story-7"}),
                _resp({"data": []}),
            ]
        )

    def test_start_upload_poll_finish(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *_: None)
        provider = _fb()
        self._flow(
            provider,
            [
                {"video_status": "upload_in_progress", "uploading_phase": {"status": "in_progress"}},
                {"video_status": "processing", "uploading_phase": {"status": "complete"}},
            ],
        )
        result = provider.publish_post("tok", _story(VIDEO_URL, "video", duration=54))
        calls = provider._request.call_args_list
        assert calls[0].args == ("POST", "https://graph.facebook.com/v25.0/page-1/video_stories")
        assert calls[0].kwargs["json"] == {"upload_phase": "start"}
        assert calls[1].args == ("POST", "https://rupload.facebook.com/video-upload/v25.0/vid-1")
        assert calls[1].kwargs["headers"] == {"Authorization": "OAuth tok", "file_url": VIDEO_URL}
        assert calls[2].args[1].endswith("/vid-1")
        assert calls[2].kwargs["params"] == {"fields": "status"}
        finish = calls[4]
        assert finish.kwargs["json"] == {"upload_phase": "finish", "video_id": "vid-1"}
        assert result.platform_post_id == "story-7"
        assert result.url == "https://www.facebook.com/stories/page-1/"
        assert result.extra["media_type"] == "video"

    def test_upload_error_stops_before_finish(self):
        provider = _fb()
        self._flow(provider, [{"video_status": "error", "uploading_phase": {"status": "error"}}])
        with pytest.raises(PublishError, match="nicht verarbeitet"):
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=20))
        assert not any(
            c.kwargs.get("json", {}) == {"upload_phase": "finish", "video_id": "vid-1"}
            for c in provider._request.call_args_list
        )

    def test_start_without_upload_url_is_a_publish_error(self):
        provider = _fb()
        provider._request = MagicMock(side_effect=[_resp({"video_id": "vid-1"})])
        with pytest.raises(PublishError, match="upload_url"):
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=20))

    def test_finish_without_post_id_is_a_publish_error(self):
        provider = _fb()
        provider._request = MagicMock(
            side_effect=[
                _resp({"video_id": "vid-1", "upload_url": "https://rupload.facebook.com/x"}),
                _resp({"success": True}),
                _resp({"status": {"uploading_phase": {"status": "complete"}}}),
                _resp({"success": False}),
            ]
        )
        with pytest.raises(PublishError, match="Video-Story"):
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=20))

    def test_video_over_60_seconds_fails_before_any_request(self):
        provider = _fb()
        provider._request = MagicMock()
        with pytest.raises(PublishError, match="höchstens 60") as exc:
            provider.publish_post("tok", _story(VIDEO_URL, "video", duration=75))
        assert exc.value.retryable is False
        provider._request.assert_not_called()

    def test_story_is_a_supported_post_type(self):
        assert PostType.STORY in _fb().supported_post_types
