"""Instagram Audio API: trending lookup and attaching a sound to a reel.

Meta opened this on 2026-06-01 for apps using Facebook Login. The tests pin the
three facts that were easy to get wrong while reading the docs:

* the trending list is "GET /ig_audio without search_query", not a separate
  endpoint,
* the sound rides on the REELS container as ``audio_configuration``, a JSON
  string, and nowhere else,
* ``video_volume`` keeps our own narrator audible underneath, so choosing a
  platform sound is not an either/or.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from providers.exceptions import APIError
from providers.instagram import (
    DEFAULT_AUDIO_VOLUME,
    DEFAULT_VIDEO_VOLUME,
    InstagramProvider,
    build_audio_configuration,
)
from providers.types import PostType, PublishContent


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _provider(**creds):
    return InstagramProvider({"client_id": "id", "client_secret": "secret", **creds})


# ---------------------------------------------------------------------------
# Catalogue lookup
# ---------------------------------------------------------------------------


def test_list_audio_without_query_asks_for_trending():
    provider = _provider(ig_user_id="ig-1")
    provider._request = MagicMock(
        return_value=_resp(
            {
                "data": [
                    {
                        "id": "587784541076604",
                        "title": "Sommerregen",
                        "artist_name": "Komiku",
                        "duration_ms": 21000,
                        "display_image_uri": "https://example.com/cover.jpg",
                    }
                ]
            }
        )
    )

    tracks = provider.list_audio("page-token")

    provider._request.assert_called_once_with(
        "GET",
        "https://graph.facebook.com/v25.0/ig_audio",
        access_token="page-token",
        params={"audio_type": "music", "limit": 25, "user_id": "ig-1"},
    )
    # No search_query: the documented trigger for the trending list.
    assert "search_query" not in provider._request.call_args.kwargs["params"]
    assert tracks == [
        {
            "id": "587784541076604",
            "title": "Sommerregen",
            "artist": "Komiku",
            "duration_ms": 21000,
            "cover_url": "https://example.com/cover.jpg",
            "raw": tracks[0]["raw"],
        }
    ]


def test_list_audio_with_query_searches_the_catalogue():
    provider = _provider(ig_user_id="ig-1")
    provider._request = MagicMock(return_value=_resp({"data": []}))

    provider.list_audio("page-token", search_query="  walking shoes  ", limit=99)

    params = provider._request.call_args.kwargs["params"]
    assert params["search_query"] == "walking shoes"
    # limit is capped at the provider's MAX_AUDIO_LIMIT
    assert params["limit"] == 50


def test_list_audio_rejects_an_undocumented_audio_type():
    provider = _provider()
    provider._request = MagicMock()

    with pytest.raises(ValueError):
        provider.list_audio("page-token", audio_type="podcast")

    provider._request.assert_not_called()


def test_list_audio_reads_alternative_field_names_and_drops_idless_entries():
    provider = _provider()
    provider._request = MagicMock(
        return_value=_resp(
            {
                "data": [
                    {"audio_asset_id": "42", "display_name": "Walk", "owner": {"username": "loyalty"}},
                    {"title": "no id at all"},
                    "not-a-dict",
                ]
            }
        )
    )

    tracks = provider.list_audio("page-token", audio_type="original_sound")

    assert [t["id"] for t in tracks] == ["42"]
    assert tracks[0]["title"] == "Walk"
    assert tracks[0]["artist"] == "loyalty"
    assert tracks[0]["duration_ms"] is None


def test_get_audio_returns_normalized_metadata():
    provider = _provider()
    provider._request = MagicMock(return_value=_resp({"id": "7", "title": "Bett", "duration": "nonsense"}))

    track = provider.get_audio("page-token", "7")

    assert track["id"] == "7"
    assert track["title"] == "Bett"
    assert track["duration_ms"] is None


# ---------------------------------------------------------------------------
# audio_configuration
# ---------------------------------------------------------------------------


def test_build_audio_configuration_returns_none_without_a_sound():
    assert build_audio_configuration(None) is None
    assert build_audio_configuration({}) is None
    assert build_audio_configuration({"audio_id": "   "}) is None
    assert build_audio_configuration({"audio_volume": 40}) is None


def test_build_audio_configuration_defaults_keep_the_voice_in_front():
    config = build_audio_configuration({"audio_id": "587784541076604"})

    assert config == {
        "audio_id": "587784541076604",
        "audio_volume": DEFAULT_AUDIO_VOLUME,
        "video_volume": DEFAULT_VIDEO_VOLUME,
    }
    # The narrator (video track) must not be pushed under the platform sound.
    assert config["video_volume"] > config["audio_volume"]


def test_build_audio_configuration_clamps_and_survives_junk():
    config = build_audio_configuration({"audio_id": "1", "audio_volume": 250, "video_volume": -30})
    assert config["audio_volume"] == 100
    assert config["video_volume"] == 0

    fallback = build_audio_configuration({"audio_id": "1", "audio_volume": "loud", "video_volume": None})
    assert fallback["audio_volume"] == DEFAULT_AUDIO_VOLUME
    assert fallback["video_volume"] == DEFAULT_VIDEO_VOLUME

    # A muted platform sound is a legitimate choice, not junk.
    assert build_audio_configuration({"audio_id": "1", "audio_volume": 0})["audio_volume"] == 0


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def _reel(**extra):
    return PublishContent(
        text="Caption",
        media_urls=["https://cdn.example.com/reel.mp4"],
        post_type=PostType.REEL,
        extra={"ig_user_id": "ig-1", **extra},
    )


def test_reel_container_carries_audio_configuration_as_a_json_string():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    result = provider.publish_post(
        "page-token",
        _reel(audio_id="587784541076604", audio_volume=30, video_volume=90),
    )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "REELS"
    assert json.loads(payload["audio_configuration"]) == {
        "audio_id": "587784541076604",
        "audio_volume": 30,
        "video_volume": 90,
    }
    assert result.extra["audio_id"] == "587784541076604"
    assert "audio_dropped" not in result.extra


def test_reel_without_a_chosen_sound_sends_no_audio_configuration():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post("page-token", _reel())

    assert "audio_configuration" not in provider._request.call_args_list[0].kwargs["json"]


def test_image_post_never_carries_a_platform_sound(caplog):
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )
    content = PublishContent(
        text="Caption",
        media_urls=["https://cdn.example.com/slide.jpg"],
        post_type=PostType.IMAGE,
        extra={"ig_user_id": "ig-1", "audio_id": "587784541076604"},
    )

    result = provider.publish_post("page-token", content)

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert "audio_configuration" not in payload
    assert "audio_id" not in result.extra
    assert "reels only" in caplog.text.lower()


def test_withdrawn_track_falls_back_to_publishing_without_sound(caplog):
    """The third-party catalogue is a moving subset of the app's. A track that
    disappeared must cost the sound, not the reel."""
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            APIError("Instagram API error 400: invalid audio_id", platform="Instagram"),
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    result = provider.publish_post("page-token", _reel(audio_id="gone-for-good"))

    assert result.platform_post_id == "media-1"
    assert result.extra["audio_dropped"] is True
    retry_payload = provider._request.call_args_list[1].kwargs["json"]
    assert "audio_configuration" not in retry_payload
    assert retry_payload["video_url"] == "https://cdn.example.com/reel.mp4"
    assert "retrying without sound" in caplog.text


# ---------------------------------------------------------------------------
# Live check command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_audio_check_command_says_what_is_missing_without_a_connected_account():
    """The live lookup needs a Facebook-Login account. Without one the command
    must say so plainly instead of pretending the catalogue is empty."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError, match="No connected Instagram account"):
        call_command("instagram_audio_check")


@pytest.mark.django_db
def test_audio_check_command_prints_tracks(capsys):
    from django.core.management import call_command

    from apps.organizations.models import Organization
    from apps.social_accounts.models import SocialAccount
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="Audio Org")
    workspace = Workspace.objects.create(organization=org, name="Audio WS")
    SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="17841400000000000",
        account_name="Orbita Media",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        oauth_access_token="page-token",
    )

    provider = MagicMock()
    provider.list_audio.return_value = [
        {"id": "42", "title": "Sommerregen", "artist": "Komiku", "duration_ms": 21000, "cover_url": "", "raw": {}}
    ]
    with patch("apps.analytics.tasks._resolve_provider", return_value=provider):
        call_command("instagram_audio_check")

    out = capsys.readouterr().out
    assert "Sommerregen" in out
    assert "42" in out


def test_container_failure_without_audio_still_raises():
    provider = _provider()
    provider._request = MagicMock(side_effect=APIError("Instagram API error 400", platform="Instagram"))

    with pytest.raises(APIError):
        provider.publish_post("page-token", _reel())

    assert provider._request.call_count == 1
