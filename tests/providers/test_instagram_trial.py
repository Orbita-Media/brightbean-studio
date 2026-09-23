"""Instagram trial reels ("Test-Reel" in the app): non-followers first.

The tests pin what was easy to get wrong:

* ``trial_params`` rides on the REELS container as a JSON *string*
  (same trap as ``audio_configuration`` and ``collaborators``),
* SS_PERFORMANCE is the default, MANUAL is selectable, spelling is forgiving,
* the documented constraint "media_type must be REELS" – a trial on a story,
  an image or a carousel FAILS before any upload instead of quietly going out
  to every follower,
* both ways to reach the account (Facebook Login and Instagram Login) behave
  the same, including a lone video (PostType.VIDEO) taking the reel path.
"""

import json
from unittest.mock import MagicMock

import pytest

from providers.exceptions import PublishError
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.instagram_trial import (
    DEFAULT_GRADUATION,
    GRADUATION_MANUAL,
    GRADUATION_SS_PERFORMANCE,
    build_trial_params,
    is_trial,
    normalize_graduation,
)
from providers.types import PostType, PublishContent

REEL_URL = "https://cdn.example.com/reel.mp4"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _graph():
    return InstagramProvider({"client_id": "id", "client_secret": "secret"})


def _login():
    return InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})


def _content(post_type, media_urls, **extra):
    return PublishContent(
        text="Caption",
        media_urls=media_urls,
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


# ---------------------------------------------------------------------------
# Reading the setting
# ---------------------------------------------------------------------------


def test_no_trial_is_the_normal_case():
    assert is_trial(None) is False
    assert is_trial({}) is False
    assert is_trial({"trial": False}) is False
    assert is_trial({"trial": ""}) is False
    assert is_trial({"trial": "false"}) is False
    assert is_trial({"trial": None}) is False
    assert build_trial_params({}, PostType.REEL) is None


def test_trial_accepts_bool_and_form_strings():
    assert is_trial({"trial": True})
    for raw in ("true", "True", "1", "on", "ja", " yes "):
        assert is_trial({"trial": raw}), raw
    assert is_trial({"trial": 1})


def test_default_strategy_is_automatic_graduation():
    assert DEFAULT_GRADUATION == GRADUATION_SS_PERFORMANCE
    assert build_trial_params({"trial": True}, PostType.REEL) == {"graduation_strategy": "SS_PERFORMANCE"}
    assert normalize_graduation("") == "SS_PERFORMANCE"
    assert normalize_graduation(None) == "SS_PERFORMANCE"


def test_manual_is_selectable_and_spelling_is_forgiving():
    assert build_trial_params({"trial": True, "trial_graduation": "MANUAL"}, PostType.REEL) == {
        "graduation_strategy": GRADUATION_MANUAL
    }
    assert normalize_graduation(" manual ") == "MANUAL"
    assert normalize_graduation("ss_performance") == "SS_PERFORMANCE"


def test_unknown_strategy_is_refused_not_guessed():
    assert normalize_graduation("AUTO") is None
    with pytest.raises(PublishError, match="Unbekannte Freigabe"):
        build_trial_params({"trial": True, "trial_graduation": "AUTO"}, PostType.REEL)


def test_strategy_without_trial_switch_does_nothing():
    # A leftover choice must not turn a normal reel into a trial.
    assert build_trial_params({"trial_graduation": "MANUAL"}, PostType.REEL) is None


# ---------------------------------------------------------------------------
# Facebook-Login path (InstagramProvider)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("post_type", [PostType.REEL, PostType.VIDEO])
def test_reel_container_carries_trial_params_as_a_json_string(post_type):
    provider = _graph()
    _publish_flow(provider)

    result = provider.publish_post("page-token", _content(post_type, [REEL_URL], trial=True))

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "REELS"
    assert isinstance(payload["trial_params"], str)
    assert json.loads(payload["trial_params"]) == {"graduation_strategy": "SS_PERFORMANCE"}
    assert result.extra["trial_graduation"] == "SS_PERFORMANCE"


def test_manual_graduation_reaches_the_container():
    provider = _graph()
    _publish_flow(provider)

    provider.publish_post("page-token", _content(PostType.REEL, [REEL_URL], trial=True, trial_graduation="MANUAL"))

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert json.loads(payload["trial_params"]) == {"graduation_strategy": "MANUAL"}


def test_reel_without_trial_sends_no_trial_params():
    provider = _graph()
    _publish_flow(provider)

    result = provider.publish_post("page-token", _content(PostType.REEL, [REEL_URL]))

    assert "trial_params" not in provider._request.call_args_list[0].kwargs["json"]
    assert "trial_graduation" not in result.extra


def test_trial_combines_with_collaborators_and_sound():
    provider = _graph()
    _publish_flow(provider)

    provider.publish_post(
        "page-token",
        _content(PostType.REEL, [REEL_URL], trial=True, collaborators=["autorin"], audio_id="a-1"),
    )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert json.loads(payload["trial_params"]) == {"graduation_strategy": "SS_PERFORMANCE"}
    assert json.loads(payload["collaborators"]) == ["autorin"]
    assert json.loads(payload["audio_configuration"])["audio_id"] == "a-1"


def test_withdrawn_sound_retry_keeps_the_trial():
    # The sound fallback retries the container without audio. The trial must
    # survive that retry, otherwise the reel would reach every follower.
    from providers.exceptions import APIError

    provider = _graph()
    provider._request = MagicMock(
        side_effect=[
            APIError("audio gone", platform="Instagram"),
            _resp({"id": "container-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post("page-token", _content(PostType.REEL, [REEL_URL], trial=True, audio_id="a-1"))

    retry_payload = provider._request.call_args_list[1].kwargs["json"]
    assert "audio_configuration" not in retry_payload
    assert json.loads(retry_payload["trial_params"]) == {"graduation_strategy": "SS_PERFORMANCE"}


@pytest.mark.parametrize(
    ("post_type", "url"),
    [
        (PostType.IMAGE, "https://cdn.example.com/cover.jpg"),
        (PostType.STORY, "https://cdn.example.com/story.mp4"),
    ],
)
def test_trial_on_something_else_than_a_reel_fails_before_upload(post_type, url):
    provider = _graph()
    provider._request = MagicMock()

    with pytest.raises(PublishError, match="Test-Reel geht nur bei einem Reel"):
        provider.publish_post("page-token", _content(post_type, [url], trial=True))

    provider._request.assert_not_called()


def test_trial_on_a_carousel_fails_before_the_first_child_upload():
    provider = _graph()
    provider._request = MagicMock()

    with pytest.raises(PublishError, match="Test-Reel"):
        provider.publish_post(
            "page-token",
            _content(PostType.CAROUSEL, ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"], trial=True),
        )

    provider._request.assert_not_called()


# ---------------------------------------------------------------------------
# Instagram-Login path (InstagramLoginProvider) – same account, second way in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("post_type", [PostType.REEL, PostType.VIDEO])
def test_login_path_reel_carries_trial_params(post_type):
    provider = _login()
    _publish_flow(provider)

    provider.publish_post("token", _content(post_type, [REEL_URL], trial=True, trial_graduation="manual"))

    first = provider._request.call_args_list[0]
    assert first.args[1].endswith("/me/media")
    payload = first.kwargs["json"]
    assert payload["media_type"] == "REELS"
    assert payload["video_url"] == REEL_URL
    assert "image_url" not in payload
    assert json.loads(payload["trial_params"]) == {"graduation_strategy": "MANUAL"}


def test_login_path_lone_video_without_trial_is_a_reel_not_an_image():
    provider = _login()
    _publish_flow(provider)

    provider.publish_post("token", _content(PostType.VIDEO, [REEL_URL]))

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "REELS"
    assert "trial_params" not in payload


def test_login_path_trial_on_image_fails_before_upload():
    provider = _login()
    provider._request = MagicMock()

    with pytest.raises(PublishError, match="Test-Reel"):
        provider.publish_post("token", _content(PostType.IMAGE, ["https://cdn.example.com/cover.jpg"], trial=True))

    provider._request.assert_not_called()


def test_login_path_trial_on_carousel_fails_before_upload():
    provider = _login()
    provider._request = MagicMock()

    with pytest.raises(PublishError, match="Test-Reel"):
        provider.publish_post(
            "token",
            _content(PostType.CAROUSEL, ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"], trial=True),
        )

    provider._request.assert_not_called()
