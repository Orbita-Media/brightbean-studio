"""Which publish errors leave the outcome open, and how a live post is found again.

Pins the provider half of the double-post protection (the engine half is in
apps/publisher/test_outcome_unknown.py): only the request that makes a post
public is guarded, an unclear answer there becomes
``PublishOutcomeUnknownError``, and the lookups find the post by its text in
the attempt's time window.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import pytest

from providers.bluesky import BlueskyProvider
from providers.devto import DevtoProvider
from providers.exceptions import (
    APIError,
    PublishError,
    PublishLookupUnavailableError,
    PublishOutcomeUnknownError,
    RateLimitError,
)
from providers.facebook import FacebookProvider
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.linkedin_personal import LinkedInPersonalProvider
from providers.mastodon import MastodonProvider
from providers.pinterest import PinterestProvider
from providers.published_lookup import MAX_LOOKUP_PAGES, normalize_text, parse_time
from providers.threads import ThreadsProvider
from providers.tiktok import TikTokProvider
from providers.types import PostType, PublishContent
from providers.x import XProvider
from providers.youtube import YouTubeProvider

CAPTION = "Dein Wecker heißt Katzenverhalten\n\n#katzen #buch"
SINCE = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)

# The answer Instagram gave on 22.10.2026 although the reel was published.
IG_500 = APIError(
    'Instagram API error 500: {"error":{"message":"An unexpected error has occurred. Please retry your '
    'request later.","type":"OAuthException","is_transient":true,"code":2}}',
    status_code=500,
    platform="Instagram",
    raw_response={"error": {"message": "An unexpected error has occurred.", "code": 2, "is_transient": True}},
)


def _resp(data, headers=None):
    return MagicMock(json=MagicMock(return_value=data), headers=headers or {})


def _content(**kw):
    defaults = {
        "text": CAPTION,
        "media_urls": ["https://cdn.example.com/a.jpg"],
        "post_type": PostType.IMAGE,
        "extra": {"ig_user_id": "17841466348000992", "page_id": "page-1"},
    }
    defaults.update(kw)
    return PublishContent(**defaults)


# ---------------------------------------------------------------------------
# publish_commit: which errors may hide a live post
# ---------------------------------------------------------------------------


class TestPublishCommitClassification:
    provider = XProvider({})

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ReadTimeout("The read operation timed out"),
            httpx.WriteTimeout("write timed out"),
            httpx.RemoteProtocolError("Server disconnected without sending a response."),
            httpx.ReadError("connection reset"),
            APIError("X API error 500", status_code=500),
            APIError("X API error 503", status_code=503),
            KeyError("id"),
            ValueError("Expecting value: line 1 column 1 (char 0)"),
            PublishError("answered without an id"),
        ],
    )
    def test_unclear_errors_become_outcome_unknown(self, error):
        with pytest.raises(PublishOutcomeUnknownError) as excinfo, self.provider.publish_commit():
            raise error
        assert excinfo.value.__cause__ is error
        assert excinfo.value.retryable is False
        assert "unklar" in str(excinfo.value)

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
            httpx.ConnectTimeout("connect timed out"),
            httpx.PoolTimeout("no connection free"),
            APIError("X API error 400", status_code=400),
            APIError("X API error 401", status_code=401),
            RateLimitError("429"),
        ],
    )
    def test_errors_proving_nothing_arrived_pass_unchanged(self, error):
        with pytest.raises(type(error)) as excinfo, self.provider.publish_commit():
            raise error
        assert excinfo.value is error

    def test_meta_unknown_error_under_400_is_unclear(self):
        error = APIError(
            "Threads API error 400",
            status_code=400,
            raw_response={"error": {"message": "An unknown error occurred", "code": 1}},
        )
        with pytest.raises(PublishOutcomeUnknownError), ThreadsProvider({}).publish_commit():
            raise error

    def test_meta_refusal_under_400_passes(self):
        error = APIError(
            "Instagram API error 400",
            status_code=400,
            raw_response={"error": {"message": "Media ID is not available", "code": 9007}},
        )
        with pytest.raises(APIError), InstagramProvider({}).publish_commit():
            raise error


# ---------------------------------------------------------------------------
# Instagram: container errors stay retryable, media_publish is guarded
# ---------------------------------------------------------------------------


class TestInstagramCommit:
    def _provider(self, *responses):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=list(responses))
        return provider

    def test_500_on_media_publish_is_outcome_unknown(self):
        """22.10.2026: media_publish 500 'unexpected error' although the reel was live."""
        provider = self._provider(
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            IG_500,
        )
        with pytest.raises(PublishOutcomeUnknownError) as excinfo:
            provider.publish_post("tok", _content())
        assert excinfo.value.__cause__ is IG_500
        assert provider._request.call_args_list[2].args[1].endswith("/media_publish")

    def test_500_on_container_creation_stays_a_plain_retryable_error(self):
        """Nothing is public before media_publish: repeating the container is harmless."""
        error = APIError("Instagram API error 500", status_code=500, raw_response={"error": {"code": 2}})
        provider = self._provider(error)
        with pytest.raises(APIError) as excinfo:
            provider.publish_post("tok", _content())
        assert excinfo.value is error
        assert excinfo.value.retryable is True

    def test_media_publish_without_id_is_outcome_unknown(self):
        provider = self._provider(
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({}),
        )
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post("tok", _content())

    def test_carousel_publish_timeout_is_outcome_unknown(self):
        provider = self._provider(
            _resp({"id": "child-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "child-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "carousel-1"}),
            _resp({"status_code": "FINISHED"}),
            httpx.ReadTimeout("The read operation timed out"),
        )
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post(
                "tok",
                _content(
                    media_urls=["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"],
                    post_type=PostType.CAROUSEL,
                ),
            )

    def test_instagram_login_media_publish_is_guarded(self):
        provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=[httpx.ReadTimeout("timed out")])
        with pytest.raises(PublishOutcomeUnknownError):
            provider._publish_container("tok", "container-1")


# ---------------------------------------------------------------------------
# Instagram lookup
# ---------------------------------------------------------------------------


def _ig_media(media_id, caption, ts):
    return {
        "id": media_id,
        "caption": caption,
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "permalink": f"https://www.instagram.com/p/{media_id}/",
    }


class TestInstagramLookup:
    def _provider(self, *pages):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=[_resp(p) for p in pages])
        return provider

    def test_finds_the_reel_by_caption_inside_the_window(self):
        provider = self._provider(
            {
                "data": [
                    _ig_media("other", "Ganz anderer Text", SINCE + timedelta(minutes=2)),
                    # Instagram trims and normalises whitespace; still the same post.
                    _ig_media("18112059887156840", CAPTION.replace("\n\n", "\n") + " ", SINCE + timedelta(seconds=75)),
                    _ig_media("old", CAPTION, SINCE - timedelta(days=3)),
                ]
            }
        )
        found = provider.find_published_post("tok", _content(), since=SINCE)
        assert found.platform_post_id == "18112059887156840"
        assert found.url == "https://www.instagram.com/p/18112059887156840/"
        call = provider._request.call_args
        assert call.args[1].endswith("/17841466348000992/media")
        assert "caption" in call.kwargs["params"]["fields"]

    def test_older_post_with_same_caption_is_not_a_match(self):
        provider = self._provider({"data": [_ig_media("old", CAPTION, SINCE - timedelta(minutes=1))]})
        assert provider.find_published_post("tok", _content(), since=SINCE) is None

    def test_returns_the_earliest_of_two_copies(self):
        provider = self._provider(
            {
                "data": [
                    _ig_media("second", CAPTION, SINCE + timedelta(minutes=3)),
                    _ig_media("first", CAPTION, SINCE + timedelta(minutes=1)),
                ]
            }
        )
        assert provider.find_published_post("tok", _content(), since=SINCE).platform_post_id == "first"

    def test_ids_of_other_rows_are_skipped(self):
        provider = self._provider({"data": [_ig_media("taken", CAPTION, SINCE + timedelta(minutes=1))]})
        assert provider.find_published_post("tok", _content(), since=SINCE, exclude_ids={"taken"}) is None

    def test_follows_the_cursor_until_the_window_is_covered(self):
        provider = self._provider(
            {
                "data": [_ig_media("n1", "neu", SINCE + timedelta(minutes=9))],
                "paging": {"cursors": {"after": "CUR1"}, "next": "https://graph.facebook.com/next"},
            },
            {
                "data": [
                    _ig_media("hit", CAPTION, SINCE + timedelta(minutes=1)),
                    _ig_media("o", "alt", SINCE - timedelta(hours=1)),
                ]
            },
        )
        assert provider.find_published_post("tok", _content(), since=SINCE).platform_post_id == "hit"
        assert provider._request.call_args_list[1].kwargs["params"]["after"] == "CUR1"

    def test_window_too_full_cannot_be_checked(self):
        page = {
            "data": [_ig_media("n", "neu", SINCE + timedelta(minutes=5))],
            "paging": {"cursors": {"after": "C"}, "next": "https://graph.facebook.com/next"},
        }
        provider = self._provider(*([page] * MAX_LOOKUP_PAGES))
        with pytest.raises(PublishLookupUnavailableError, match="zu viele"):
            provider.find_published_post("tok", _content(), since=SINCE)

    def test_story_cannot_be_checked(self):
        provider = self._provider()
        with pytest.raises(PublishLookupUnavailableError, match="Story"):
            provider.find_published_post("tok", _content(post_type=PostType.STORY), since=SINCE)
        provider._request.assert_not_called()

    def test_post_without_caption_cannot_be_checked(self):
        provider = self._provider({"data": []})
        with pytest.raises(PublishLookupUnavailableError, match="keinen Text"):
            provider.find_published_post("tok", _content(text=""), since=SINCE)

    def test_instagram_login_reads_its_own_media_edge(self):
        provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(
            return_value=_resp({"data": [_ig_media("ig-l", CAPTION, SINCE + timedelta(minutes=1))]})
        )
        assert provider.find_published_post("tok", _content(), since=SINCE).platform_post_id == "ig-l"
        assert provider._request.call_args.args[1].endswith("/me/media")


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------


class TestFacebook:
    def _provider(self, *responses):
        provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=list(responses))
        return provider

    def test_photo_upload_timeout_is_outcome_unknown(self):
        """15.10.2026: 'The read operation timed out' on a photo that had published."""
        provider = self._provider(httpx.ReadTimeout("The read operation timed out"))
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post("tok", _content())

    def test_photo_upload_connect_error_stays_retryable(self):
        error = httpx.ConnectError("[Errno -3] Temporary failure in name resolution")
        provider = self._provider(error)
        with pytest.raises(httpx.ConnectError):
            provider.publish_post("tok", _content())

    def test_text_post_server_error_is_outcome_unknown(self):
        provider = self._provider(APIError("Facebook API error 500", status_code=500))
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post("tok", _content(media_urls=[], post_type=PostType.TEXT))

    def test_unclear_multi_photo_feed_keeps_the_staged_photos(self):
        """The post may be live with exactly these photos: deleting them would gut it."""
        provider = self._provider(
            _resp({"id": "photo-1"}),
            _resp({"id": "photo-2"}),
            httpx.ReadTimeout("The read operation timed out"),
        )
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post(
                "tok",
                _content(media_urls=["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]),
            )
        assert all(c.args[0] != "DELETE" for c in provider._request.call_args_list)

    def test_video_upload_timeout_is_outcome_unknown(self):
        provider = self._provider(httpx.ReadTimeout("timed out"))
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post(
                "tok", _content(media_urls=["https://cdn.example.com/v.mp4"], post_type=PostType.VIDEO)
            )

    def test_lookup_reads_published_posts_since_the_window(self):
        provider = self._provider(
            _resp(
                {
                    "data": [
                        {
                            "id": "1345695615293726_122112033195495946",
                            "message": CAPTION,
                            "created_time": (SINCE + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                            "permalink_url": "https://www.facebook.com/1345695615293726/posts/122112033195495946",
                        }
                    ]
                }
            )
        )
        found = provider.find_published_post("tok", _content(), since=SINCE)
        # Stored like a fresh publish: the post object id without the page prefix.
        assert found.platform_post_id == "122112033195495946"
        assert found.url.endswith("/posts/122112033195495946")
        call = provider._request.call_args
        assert call.args[1].endswith("/page-1/published_posts")
        assert call.kwargs["params"]["since"] == int(SINCE.timestamp())

    def test_lookup_exclusion_uses_the_stored_id_format(self):
        provider = self._provider(
            _resp(
                {
                    "data": [
                        {
                            "id": "page-1_taken",
                            "message": CAPTION,
                            "created_time": (SINCE + timedelta(minutes=1)).isoformat(),
                        }
                    ]
                }
            )
        )
        assert provider.find_published_post("tok", _content(), since=SINCE, exclude_ids={"taken"}) is None

    def test_story_cannot_be_checked(self):
        with pytest.raises(PublishLookupUnavailableError):
            self._provider().find_published_post("tok", _content(post_type=PostType.STORY), since=SINCE)


# ---------------------------------------------------------------------------
# Threads, Bluesky, YouTube
# ---------------------------------------------------------------------------


class TestThreads:
    def _provider(self, *responses):
        provider = ThreadsProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=list(responses))
        return provider

    def test_500_on_threads_publish_is_outcome_unknown(self):
        error = APIError("Threads API error 500", status_code=500, raw_response={"error": {"code": 2}})
        provider = self._provider(_resp({"id": "creation-1"}), error)
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post("tok", _content(media_urls=[], post_type=PostType.TEXT, extra={"user_id": "u1"}))

    def test_500_on_container_creation_stays_retryable(self):
        error = APIError("Threads API error 500", status_code=500, raw_response={"error": {"code": 2}})
        provider = self._provider(error)
        with pytest.raises(APIError) as excinfo:
            provider.publish_post("tok", _content(media_urls=[], post_type=PostType.TEXT, extra={"user_id": "u1"}))
        assert excinfo.value is error

    def test_lookup_reads_the_profile_threads(self):
        provider = self._provider(
            _resp(
                {
                    "data": [
                        {
                            "id": "thread-1",
                            "text": CAPTION,
                            "timestamp": (SINCE + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                            "permalink": "https://www.threads.net/@x/post/abc",
                        }
                    ]
                }
            )
        )
        found = provider.find_published_post("tok", _content(extra={"user_id": "u1"}), since=SINCE)
        assert found.platform_post_id == "thread-1"
        assert provider._request.call_args.args[1].endswith("/u1/threads")


class TestBluesky:
    def _provider(self, *responses):
        provider = BlueskyProvider({})
        provider._request = MagicMock(side_effect=list(responses))
        return provider

    def test_create_record_timeout_is_outcome_unknown(self):
        provider = self._provider(httpx.ReadTimeout("The read operation timed out"))
        with pytest.raises(PublishOutcomeUnknownError):
            provider._create_post_record("tok", did="did:plc:x", text="Hallo", embed=None)

    def test_upstream_failure_is_outcome_unknown(self):
        provider = self._provider(APIError("Bluesky API error 502: UpstreamFailure", status_code=502))
        with pytest.raises(PublishOutcomeUnknownError):
            provider._create_post_record("tok", did="did:plc:x", text="Hallo", embed=None)

    def test_lookup_reads_the_repo_records(self):
        uri = "at://did:plc:x/app.bsky.feed.post/3mwtilehlyc2y"
        provider = self._provider(
            _resp({"did": "did:plc:x", "handle": "orbita.bsky.social"}),
            _resp(
                {
                    "records": [
                        {"uri": uri, "value": {"text": CAPTION, "createdAt": "2026-10-22T12:00:30.123456Z"}},
                        {"uri": "at://old", "value": {"text": CAPTION, "createdAt": "2026-10-20T12:00:30Z"}},
                    ],
                    "cursor": "next",
                }
            ),
        )
        found = provider.find_published_post("tok", _content(), since=SINCE)
        assert found.platform_post_id == uri
        assert found.url == "https://bsky.app/profile/orbita.bsky.social/post/3mwtilehlyc2y"


class TestYouTube:
    def test_upload_server_error_is_outcome_unknown(self, tmp_path):
        video = tmp_path / "v.mp4"
        video.write_bytes(b"0000")
        provider = YouTubeProvider({})
        provider._request = MagicMock(
            side_effect=[
                MagicMock(headers={"Location": "https://upload.example/session"}),
                APIError("YouTube API error 500", status_code=500),
            ]
        )
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post(
                "tok",
                PublishContent(text="Titel", media_files=[str(video)], post_type=PostType.VIDEO),
            )

    def test_upload_session_error_stays_retryable(self):
        error = APIError("YouTube API error 500", status_code=500)
        provider = YouTubeProvider({})
        provider._request = MagicMock(side_effect=[error])
        with pytest.raises(APIError) as excinfo:
            provider.publish_post("tok", PublishContent(text="Titel", media_files=["/x.mp4"], post_type=PostType.VIDEO))
        assert excinfo.value is error

    def test_lookup_reads_the_uploads_playlist(self):
        provider = YouTubeProvider({})
        provider._request = MagicMock(
            side_effect=[
                _resp({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]}),
                _resp(
                    {
                        "items": [
                            {
                                "snippet": {
                                    "title": "Titel #Shorts",
                                    "publishedAt": "2026-10-22T12:01:00Z",
                                    "resourceId": {"videoId": "O-IQcZV7rI4"},
                                }
                            }
                        ]
                    }
                ),
            ]
        )
        found = provider.find_published_post("tok", PublishContent(text="Titel", post_type=PostType.SHORT), since=SINCE)
        assert found.platform_post_id == "O-IQcZV7rI4"
        assert provider._request.call_args.kwargs["params"]["playlistId"] == "UU123"


# ---------------------------------------------------------------------------
# Providers without a lookup: guarded commit, lookup refuses
# ---------------------------------------------------------------------------


class TestProvidersWithoutLookup:
    @pytest.mark.parametrize(
        "provider",
        [
            XProvider({}),
            MastodonProvider({"instance_url": "https://mastodon.example"}),
            DevtoProvider({}),
            LinkedInPersonalProvider({}),
            PinterestProvider({}),
            TikTokProvider({"client_key": "k", "client_secret": "s"}),
        ],
    )
    def test_lookup_is_unavailable(self, provider):
        with pytest.raises(PublishLookupUnavailableError):
            provider.find_published_post("tok", _content(), since=SINCE)

    def test_x_tweet_timeout_is_outcome_unknown(self):
        provider = XProvider({})
        provider._request = MagicMock(side_effect=[httpx.ReadTimeout("timed out")])
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post("tok", PublishContent(text="Hallo", post_type=PostType.TEXT))

    def test_linkedin_post_server_error_is_outcome_unknown(self):
        provider = LinkedInPersonalProvider({})
        provider._request = MagicMock(side_effect=[APIError("LinkedIn API error 500", status_code=500)])
        with pytest.raises(PublishOutcomeUnknownError):
            provider._create_post("tok", {"author": "urn:li:person:1"})

    def test_linkedin_post_without_urn_is_outcome_unknown(self):
        provider = LinkedInPersonalProvider({})
        provider._request = MagicMock(return_value=_resp({}, headers={}))
        with pytest.raises(PublishOutcomeUnknownError):
            provider._create_post("tok", {"author": "urn:li:person:1"})

    def test_pinterest_pin_timeout_is_outcome_unknown(self):
        provider = PinterestProvider({})
        provider._request = MagicMock(side_effect=[httpx.ReadTimeout("timed out")])
        with pytest.raises(PublishOutcomeUnknownError):
            provider._create_pin("tok", {"board_id": "b"})

    def test_pinterest_400_still_reaches_the_carousel_fallback(self):
        error = APIError("Pinterest API error 400", status_code=400)
        provider = PinterestProvider({})
        provider._request = MagicMock(side_effect=[error])
        with pytest.raises(APIError) as excinfo:
            provider._create_pin("tok", {"board_id": "b"})
        assert excinfo.value is error

    def test_mastodon_status_server_error_is_outcome_unknown(self):
        provider = MastodonProvider({"instance_url": "https://mastodon.example"})
        provider._request = MagicMock(side_effect=[APIError("Mastodon API error 502", status_code=502)])
        with pytest.raises(PublishOutcomeUnknownError):
            provider.publish_post("tok", PublishContent(text="Hallo", post_type=PostType.TEXT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_normalize_text_ignores_whitespace_and_unicode_form():
    assert normalize_text("Katze\r\n\r\n  #buch ") == normalize_text("Katze\n#buch")
    assert normalize_text("Erschöpfung") == normalize_text("Erschöpfung")
    assert normalize_text(None) == ""


@pytest.mark.parametrize(
    "value",
    ["2026-10-22T12:01:15+0000", "2026-10-22T12:01:15Z", "2026-10-22T12:01:15+00:00", 1792670475],
)
def test_parse_time_reads_the_platform_formats(value):
    parsed = parse_time(value)
    assert parsed.tzinfo is not None
    assert parsed.astimezone(UTC).date().isoformat() == "2026-10-22"
