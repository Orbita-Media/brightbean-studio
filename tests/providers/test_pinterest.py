"""Pinterest carousel pins.

Until 05.08.2026 this provider pinned slide one of six and logged the rest
away. That was never a Pinterest limit: the official OpenAPI declares
``multiple_image_urls`` with 2 to 5 items. Sources: docs/PLATTFORM-GRENZEN.md.

Pinterest contradicts itself here (a guide page claims organic pins were
"simplified to image or video Pins", the spec still defines the carousel), so
the provider falls back to a single image pin on HTTP 400 rather than losing
the post – and records that it did.
"""

from unittest.mock import MagicMock

import pytest

from providers.exceptions import APIError, PublishError
from providers.pinterest import MAX_CAROUSEL_ITEMS, PinterestProvider
from providers.types import PostType, PublishContent


def _content(anzahl: int) -> PublishContent:
    return PublishContent(
        post_type=PostType.PIN,
        title="Sechs Wege",
        description="Beschreibung",
        media_urls=[f"https://example.test/{i}.jpg" for i in range(1, anzahl + 1)],
        extra={"board_id": "b1"},
    )


def _provider(*, carousel_status: int | None = None) -> PinterestProvider:
    """Provider that answers like Pinterest; ``carousel_status`` rejects carousels."""
    provider = PinterestProvider()
    provider.payloads = []

    def fake_request(method, url, **kwargs):
        payload = kwargs.get("json", {})
        provider.payloads.append(payload)
        quelle = payload.get("media_source", {}).get("source_type")
        if carousel_status and quelle == "multiple_image_urls":
            raise APIError(
                "Pinterest API error 400: unsupported media source",
                status_code=carousel_status,
                platform="Pinterest",
            )
        return MagicMock(json=MagicMock(return_value={"id": "pin1"}))

    provider._request = MagicMock(side_effect=fake_request)
    return provider


class TestCarouselPin:
    def test_five_slides_become_one_carousel_pin(self):
        provider = _provider()

        provider.publish_post("token", _content(5))

        quelle = provider.payloads[-1]["media_source"]
        assert quelle["source_type"] == "multiple_image_urls"
        assert len(quelle["items"]) == 5

    def test_every_item_carries_its_url_in_order(self):
        provider = _provider()

        provider.publish_post("token", _content(3))

        items = provider.payloads[-1]["media_source"]["items"]
        assert [item["url"] for item in items] == [f"https://example.test/{i}.jpg" for i in range(1, 4)]

    def test_the_first_slide_stays_the_cover(self):
        # Slide one is the hook; anything else as the cover breaks the post.
        provider = _provider()

        provider.publish_post("token", _content(4))

        assert provider.payloads[-1]["media_source"]["index"] == 0

    def test_a_single_image_stays_a_plain_image_pin(self):
        provider = _provider()

        provider.publish_post("token", _content(1))

        quelle = provider.payloads[-1]["media_source"]
        assert quelle == {"source_type": "image_url", "url": "https://example.test/1.jpg"}

    def test_six_slides_fail_before_anything_is_pinned(self):
        provider = _provider()

        with pytest.raises(PublishError, match="höchstens 5 Bilder"):
            provider.publish_post("token", _content(MAX_CAROUSEL_ITEMS + 1))

        assert provider.payloads == []

    def test_the_overflow_error_is_permanent(self):
        provider = _provider()

        with pytest.raises(PublishError) as fehler:
            provider.publish_post("token", _content(6))

        assert fehler.value.retryable is False


class TestCarouselFallback:
    def test_a_rejected_carousel_becomes_a_single_image_pin(self):
        provider = _provider(carousel_status=400)

        result = provider.publish_post("token", _content(4))

        assert result.platform_post_id == "pin1"
        assert provider.payloads[-1]["media_source"] == {
            "source_type": "image_url",
            "url": "https://example.test/1.jpg",
        }

    def test_the_fallback_is_recorded_in_the_result(self):
        # A carousel that quietly became one image is exactly the shrinkage
        # nobody notices. So it is written down.
        provider = _provider(carousel_status=400)

        result = provider.publish_post("token", _content(4))

        assert result.extra["carousel_rejected"] is True
        assert result.extra["carousel_dropped_images"] == 3

    def test_a_successful_carousel_is_not_marked_as_fallen_back(self):
        provider = _provider()

        result = provider.publish_post("token", _content(4))

        assert "carousel_rejected" not in result.extra

    def test_other_errors_are_not_swallowed(self):
        # Only a 400 means "carousel not supported"; a 401 is a broken token
        # and must not end as a quietly shortened pin.
        provider = _provider(carousel_status=401)

        with pytest.raises(APIError):
            provider.publish_post("token", _content(4))


class TestPinBasics:
    def test_a_pin_without_a_board_fails(self):
        provider = _provider()
        content = _content(3)
        content.extra = {}

        with pytest.raises(PublishError, match="board_id"):
            provider.publish_post("token", content)

    def test_a_pin_without_media_fails(self):
        provider = _provider()
        content = _content(3)
        content.media_urls = []

        with pytest.raises(PublishError, match="No media"):
            provider.publish_post("token", content)

    def test_the_title_is_cut_to_a_hundred_characters(self):
        provider = _provider()
        content = _content(3)
        content.title = "ü" * 150

        provider.publish_post("token", content)

        assert len(provider.payloads[-1]["title"]) == 100
