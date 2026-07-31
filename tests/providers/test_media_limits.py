"""Every provider declares how many attachments one post can actually show.

Background: Bluesky shows four images per post. Our carousels have six, so
slides 5 and 6 were dropped on every publish for weeks without a warning, a log
line or an error. Bluesky now chains the overflow into replies; for the other
channels the point of these tests is that the limit is *declared* and that
going over it is loud, never silent.
"""

import logging
from unittest.mock import MagicMock

import pytest

from apps.publisher.engine import PublishEngine
from providers import PROVIDER_REGISTRY, get_provider
from providers.bluesky import BlueskyProvider
from providers.exceptions import PublishError
from providers.google_business import GoogleBusinessProvider
from providers.instagram import MAX_CAROUSEL_ITEMS as IG_MAX_ITEMS
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.mastodon import DEFAULT_MAX_MEDIA_ATTACHMENTS, MastodonProvider
from providers.pinterest import PinterestProvider
from providers.threads import MAX_CAROUSEL_ITEMS as THREADS_MAX_ITEMS
from providers.threads import ThreadsProvider
from providers.types import PostType, PublishContent

# The documented per-post media capacity of every channel we can publish to.
# A change here is a change of a platform limit and must be argued, not typed.
ERWARTETE_GRENZEN = {
    "bluesky": 4,
    "facebook": 10,
    "instagram": 10,
    "instagram_login": 10,
    "threads": 20,
    "mastodon": 4,
    "pinterest": 1,
    "linkedin_personal": 1,
    "linkedin_company": 1,
    "google_business": 1,
    "tiktok": 1,
    "youtube": 1,
    "devto": 1,
    "x": 0,
}


class TestDeclaredLimits:
    @pytest.mark.parametrize("platform", sorted(PROVIDER_REGISTRY))
    def test_every_provider_declares_an_integer_limit(self, platform):
        provider = get_provider(platform, {})
        assert isinstance(provider.max_media_per_post, int), (
            f"{platform} does not say how many attachments fit on one post"
        )

    @pytest.mark.parametrize(("platform", "grenze"), sorted(ERWARTETE_GRENZEN.items()))
    def test_limit_matches_the_documented_platform_value(self, platform, grenze):
        assert get_provider(platform, {}).max_media_per_post == grenze

    def test_only_bluesky_spreads_the_overflow_over_further_posts(self):
        chaining = {p for p in PROVIDER_REGISTRY if get_provider(p, {}).chains_overflow_media}
        assert chaining == {"bluesky"}


class TestCarouselGuards:
    """Over the platform maximum the publish fails with a readable reason."""

    def test_instagram_refuses_more_than_ten_items(self):
        provider = InstagramProvider({"ig_user_id": "1"})
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=[f"https://example.test/{i}.jpg" for i in range(IG_MAX_ITEMS + 1)],
        )

        with pytest.raises(PublishError, match="at most 10 items"):
            provider._publish_carousel("token", "1", content)

    def test_instagram_login_refuses_more_than_ten_items(self):
        provider = InstagramLoginProvider()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=[f"https://example.test/{i}.jpg" for i in range(IG_MAX_ITEMS + 1)],
        )

        with pytest.raises(PublishError, match="at most 10 items"):
            provider._publish_carousel("token", content)

    def test_threads_refuses_more_than_twenty_items(self):
        provider = ThreadsProvider()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=[f"https://example.test/{i}.jpg" for i in range(THREADS_MAX_ITEMS + 1)],
        )

        with pytest.raises(PublishError, match="at most 20 items"):
            provider._publish_carousel("token", "1", content)

    def test_a_six_slide_carousel_passes_every_guard(self):
        # Our own format. It must not trip anything.
        urls = [f"https://example.test/{i}.jpg" for i in range(6)]
        assert len(urls) <= InstagramProvider({}).max_media_per_post
        assert len(urls) <= ThreadsProvider().max_media_per_post


class TestMastodonAttachmentLimit:
    def test_refuses_more_attachments_than_the_instance_allows(self):
        provider = MastodonProvider({"instance_url": "https://example.social"})
        provider._request = MagicMock(
            return_value=MagicMock(
                json=MagicMock(return_value={"configuration": {"statuses": {"max_media_attachments": 4}}})
            )
        )
        content = PublishContent(post_type=PostType.IMAGE, media_files=[f"/tmp/{i}.png" for i in range(6)])

        with pytest.raises(PublishError, match="at most 4 attachments"):
            provider.publish_post("token", content)

    def test_honours_an_instance_that_raised_the_cap(self):
        provider = MastodonProvider({"instance_url": "https://example.social"})
        aufrufe = []

        def fake_request(method, url, **kwargs):
            aufrufe.append(url)
            if url.endswith("/api/v2/instance"):
                return MagicMock(
                    json=MagicMock(
                        return_value={
                            "configuration": {
                                "statuses": {"max_media_attachments": 8},
                                "media_attachments": {"description_limit": 1500},
                            }
                        }
                    )
                )
            if url.endswith("/api/v2/media"):
                return MagicMock(status_code=200, json=MagicMock(return_value={"id": "m1"}))
            return MagicMock(json=MagicMock(return_value={"id": "42", "url": "https://example.social/@x/42"}))

        provider._request = MagicMock(side_effect=fake_request)
        provider._upload_media = MagicMock(return_value="m1")
        content = PublishContent(post_type=PostType.IMAGE, media_files=[f"/tmp/{i}.png" for i in range(6)])

        result = provider.publish_post("token", content)

        assert result.platform_post_id == "42"
        assert provider._upload_media.call_count == 6

    def test_stays_silent_below_the_shipped_default(self):
        # No extra round trip for the normal case.
        provider = MastodonProvider({"instance_url": "https://example.social"})
        provider.get_instance_max_media_attachments = MagicMock()
        provider._upload_media = MagicMock(return_value="m1")
        provider.get_instance_max_alt_text_length = MagicMock(return_value=1500)
        provider._request = MagicMock(return_value=MagicMock(json=MagicMock(return_value={"id": "42"})))
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(DEFAULT_MAX_MEDIA_ATTACHMENTS)],
        )

        provider.publish_post("token", content)

        provider.get_instance_max_media_attachments.assert_not_called()


class TestSingleMediaChannelsSayWhatTheyDrop:
    def test_pinterest_pins_the_first_image_and_logs_the_rest(self, caplog):
        provider = PinterestProvider()
        provider._request = MagicMock(return_value=MagicMock(json=MagicMock(return_value={"id": "pin1"})))
        content = PublishContent(
            post_type=PostType.PIN,
            title="Titel",
            media_urls=[f"https://example.test/{i}.jpg" for i in range(6)],
            extra={"board_id": "b1"},
        )

        with caplog.at_level(logging.WARNING, logger="providers.pinterest"):
            provider.publish_post("token", content)

        payload = provider._request.call_args.kwargs["json"]
        assert payload["media_source"]["url"] == "https://example.test/0.jpg"
        assert "5 of 6 attachments are dropped" in caplog.text

    def test_google_business_sends_exactly_one_media_entry(self, caplog):
        provider = GoogleBusinessProvider()
        provider._get_account_id = MagicMock(return_value="accounts/1")
        provider._get_location_id = MagicMock(return_value="accounts/1/locations/2")
        provider._request = MagicMock(return_value=MagicMock(json=MagicMock(return_value={"name": "p1"})))
        content = PublishContent(
            text="Text",
            post_type=PostType.IMAGE,
            media_urls=[f"https://example.test/{i}.jpg" for i in range(6)],
        )

        with caplog.at_level(logging.WARNING, logger="providers.google_business"):
            provider.publish_post("token", content)

        body = provider._request.call_args.kwargs["json"]
        assert len(body["media"]) == 1
        assert body["media"][0]["sourceUrl"] == "https://example.test/0.jpg"
        assert "5 of 6 attachments are dropped" in caplog.text


class TestPublisherWarning:
    """The publisher names the gap before the post goes out."""

    @staticmethod
    def _platform_post():
        return MagicMock(id="pp-1")

    def test_warns_when_a_channel_cannot_show_every_attachment(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apps.publisher.engine"):
            PublishEngine._warn_on_dropped_media(PinterestProvider(), self._platform_post(), 6)

        assert "at most 1 of 6 attachments" in caplog.text
        assert "5 will not be published" in caplog.text

    def test_stays_silent_for_bluesky_because_the_rest_becomes_a_reply(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apps.publisher.engine"):
            PublishEngine._warn_on_dropped_media(BlueskyProvider(), self._platform_post(), 6)

        assert caplog.text == ""

    def test_stays_silent_when_everything_fits(self, caplog):
        with caplog.at_level(logging.WARNING, logger="apps.publisher.engine"):
            PublishEngine._warn_on_dropped_media(ThreadsProvider(), self._platform_post(), 6)

        assert caplog.text == ""

    def test_a_provider_without_a_declared_limit_never_breaks_the_publish(self, caplog):
        provider = MagicMock()
        provider.max_media_per_post = MagicMock()  # kein echter Zahlenwert

        with caplog.at_level(logging.WARNING, logger="apps.publisher.engine"):
            PublishEngine._warn_on_dropped_media(provider, self._platform_post(), 6)

        assert caplog.text == ""
