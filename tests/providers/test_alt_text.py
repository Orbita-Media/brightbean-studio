"""Alt text pass-through across every provider whose API supports it.

The publishing engine hands one accessibility description per media item down
in ``PublishContent.media_alt_texts``, positionally aligned with
``media_urls`` / ``media_files``. These tests pin down that each provider puts
the right description on the right image, and that a missing description never
breaks a publish.
"""

from unittest.mock import MagicMock

from providers.facebook import FacebookProvider
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.linkedin import LinkedInProvider
from providers.mastodon import DEFAULT_MAX_ALT_TEXT_LENGTH, MastodonProvider
from providers.pinterest import PinterestProvider
from providers.threads import ThreadsProvider
from providers.types import PostType, PublishContent

SLIDES = ["Folie 1: Titel auf blauem Grund", "Folie 2: drei Stichpunkte", "Folie 3: Fazit"]


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


# ---------------------------------------------------------------------------
# PublishContent.alt_text_for
# ---------------------------------------------------------------------------


class TestAltTextFor:
    def test_returns_the_entry_at_the_requested_position(self):
        content = PublishContent(media_alt_texts=SLIDES)
        assert content.alt_text_for(0) == SLIDES[0]
        assert content.alt_text_for(2) == SLIDES[2]

    def test_returns_empty_string_beyond_the_list(self):
        content = PublishContent(media_alt_texts=["nur eine"])
        assert content.alt_text_for(1) == ""
        assert content.alt_text_for(99) == ""

    def test_negative_index_never_wraps_around(self):
        # Silently returning the last slide's text for index -1 would be a
        # mismatched description, which is worse than none.
        content = PublishContent(media_alt_texts=SLIDES)
        assert content.alt_text_for(-1) == ""

    def test_falls_back_to_the_legacy_single_key(self):
        content = PublishContent(extra={"alt_text": "Ein Text für alles"})
        assert content.alt_text_for(0) == "Ein Text für alles"
        assert content.alt_text_for(3) == "Ein Text für alles"

    def test_per_image_text_beats_the_legacy_key(self):
        content = PublishContent(media_alt_texts=["Genau dieses Bild"], extra={"alt_text": "Allgemein"})
        assert content.alt_text_for(0) == "Genau dieses Bild"

    def test_blank_entry_falls_through_to_the_legacy_key(self):
        content = PublishContent(media_alt_texts=["   "], extra={"alt_text": "Allgemein"})
        assert content.alt_text_for(0) == "Allgemein"

    def test_truncates_to_the_platform_limit(self):
        content = PublishContent(media_alt_texts=["ä" * 50])
        assert content.alt_text_for(0, 10) == "ä" * 10

    def test_strips_surrounding_whitespace(self):
        content = PublishContent(media_alt_texts=["  Beschreibung  "])
        assert content.alt_text_for(0) == "Beschreibung"


# ---------------------------------------------------------------------------
# Instagram (Graph API + Instagram Login)
# ---------------------------------------------------------------------------


class TestInstagramAltText:
    def test_carousel_children_each_get_their_own_alt_text(self):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(side_effect=["child-0", "child-1", "child-2", "carousel-1"])
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=["https://cdn/1.png", "https://cdn/2.png", "https://cdn/3.png"],
            media_alt_texts=SLIDES,
        )

        provider._publish_carousel("token", "ig-user", content)

        sent = [call.args[2] for call in provider._create_container.call_args_list[:3]]
        assert [payload["alt_text"] for payload in sent] == SLIDES
        assert [payload["image_url"] for payload in sent] == content.media_urls

    def test_carousel_slide_without_alt_text_omits_the_field(self):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(side_effect=["child-0", "child-1", "carousel-1"])
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=["https://cdn/1.png", "https://cdn/2.png"],
            media_alt_texts=["Nur die erste"],
        )

        provider._publish_carousel("token", "ig-user", content)

        sent = [call.args[2] for call in provider._create_container.call_args_list[:2]]
        assert sent[0]["alt_text"] == "Nur die erste"
        assert "alt_text" not in sent[1]

    def test_video_carousel_child_never_receives_alt_text(self):
        # Instagram documents alt_text as image-only; sending it on a video
        # child is rejected.
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(side_effect=["child-0", "carousel-1"])
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=["https://cdn/clip.mp4"],
            media_alt_texts=["Beschreibung"],
        )

        provider._publish_carousel("token", "ig-user", content)

        payload = provider._create_container.call_args_list[0].args[2]
        assert "alt_text" not in payload

    def test_single_image_gets_alt_text(self):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(return_value="container-1")
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["Ein Buchcover vor weissem Grund"],
        )

        provider._publish_single("token", "ig-user", content)

        payload = provider._create_container.call_args.args[2]
        assert payload["alt_text"] == "Ein Buchcover vor weissem Grund"

    def test_reel_does_not_receive_alt_text(self):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(return_value="container-1")
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.REEL,
            media_urls=["https://cdn/clip.mp4"],
            media_alt_texts=["Beschreibung"],
        )

        provider._publish_single("token", "ig-user", content)

        payload = provider._create_container.call_args.args[2]
        assert "alt_text" not in payload

    def test_alt_text_is_truncated_to_1000_characters(self):
        provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(return_value="container-1")
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["ü" * 1500],
        )

        provider._publish_single("token", "ig-user", content)

        assert len(provider._create_container.call_args.args[2]["alt_text"]) == 1000

    def test_instagram_login_carousel_children_each_get_their_own_alt_text(self):
        provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
        provider._create_container = MagicMock(side_effect=["child-0", "child-1", "child-2", "carousel-1"])
        provider._wait_for_container = MagicMock()
        provider._publish_container = MagicMock()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=["https://cdn/1.png", "https://cdn/2.png", "https://cdn/3.png"],
            media_alt_texts=SLIDES,
        )

        provider._publish_carousel("token", content)

        sent = [call.args[1] for call in provider._create_container.call_args_list[:3]]
        assert [payload["alt_text"] for payload in sent] == SLIDES


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


class TestThreadsAltText:
    def test_carousel_items_each_get_their_own_alt_text(self):
        provider = ThreadsProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(
            side_effect=[
                _resp({"id": "item-0"}),
                _resp({"id": "item-1"}),
                _resp({"id": "item-2"}),
                _resp({"id": "carousel-1"}),
                _resp({"id": "thread-1"}),
            ]
        )
        provider._wait_for_container = MagicMock()
        content = PublishContent(
            post_type=PostType.CAROUSEL,
            media_urls=["https://cdn/1.png", "https://cdn/2.png", "https://cdn/3.png"],
            media_alt_texts=SLIDES,
        )

        provider._publish_carousel("token", "user-1", content)

        item_payloads = [call.kwargs["data"] for call in provider._request.call_args_list[:3]]
        assert [payload["alt_text"] for payload in item_payloads] == SLIDES
        assert [payload["image_url"] for payload in item_payloads] == content.media_urls

    def test_single_image_thread_gets_alt_text(self):
        provider = ThreadsProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=[_resp({"id": "container-1"}), _resp({"id": "thread-1"})])
        provider._wait_for_container = MagicMock()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["Ein Zitat auf gelbem Grund"],
        )

        provider._publish_single("token", "user-1", content)

        payload = provider._request.call_args_list[0].kwargs["data"]
        assert payload["alt_text"] == "Ein Zitat auf gelbem Grund"

    def test_text_only_thread_has_no_alt_text(self):
        provider = ThreadsProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=[_resp({"id": "container-1"}), _resp({"id": "thread-1"})])
        content = PublishContent(post_type=PostType.TEXT, text="Nur Text", extra={"alt_text": "unpassend"})

        provider._publish_single("token", "user-1", content)

        payload = provider._request.call_args_list[0].kwargs["data"]
        assert "alt_text" not in payload

    def test_missing_alt_text_omits_the_field(self):
        provider = ThreadsProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(side_effect=[_resp({"id": "container-1"}), _resp({"id": "thread-1"})])
        provider._wait_for_container = MagicMock()
        content = PublishContent(post_type=PostType.IMAGE, media_urls=["https://cdn/1.png"])

        provider._publish_single("token", "user-1", content)

        assert "alt_text" not in provider._request.call_args_list[0].kwargs["data"]


# ---------------------------------------------------------------------------
# Facebook
# ---------------------------------------------------------------------------


class TestFacebookAltText:
    def test_single_photo_gets_alt_text_custom(self):
        provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(return_value=_resp({"id": "photo-1", "post_id": "page-1_post-1"}))
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["Ein aufgeschlagenes Buch"],
        )

        provider._publish_photo("token", "page-1", content)

        assert provider._request.call_args.kwargs["json"]["alt_text_custom"] == "Ein aufgeschlagenes Buch"

    def test_each_staged_photo_keeps_its_own_alt_text(self):
        provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(
            side_effect=[
                _resp({"id": "photo-1"}),
                _resp({"id": "photo-2"}),
                _resp({"id": "photo-3"}),
                _resp({"id": "page-1_post-1"}),
            ]
        )
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_urls=["https://cdn/1.png", "https://cdn/2.png", "https://cdn/3.png"],
            media_alt_texts=SLIDES,
        )

        provider._publish_multi_photo("token", "page-1", content)

        staged = [call.kwargs["json"] for call in provider._request.call_args_list[:3]]
        assert [payload["alt_text_custom"] for payload in staged] == SLIDES
        assert [payload["url"] for payload in staged] == content.media_urls

    def test_photo_without_alt_text_omits_the_field(self):
        provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(return_value=_resp({"id": "photo-1", "post_id": "page-1_post-1"}))
        content = PublishContent(post_type=PostType.IMAGE, media_urls=["https://cdn/1.png"])

        provider._publish_photo("token", "page-1", content)

        assert "alt_text_custom" not in provider._request.call_args.kwargs["json"]


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------


class TestLinkedInAltText:
    def test_image_post_carries_alt_text_on_the_media_object(self):
        provider = LinkedInProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(
            side_effect=[
                _resp({"value": {"uploadUrl": "https://upload", "image": "urn:li:image:1"}}),
                MagicMock(headers={"x-restli-id": "urn:li:share:1"}),
            ]
        )
        provider._upload_binary = MagicMock()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png"],
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["Buchcover mit Titel"],
        )

        provider._publish_image_post("token", "urn:li:person:1", content)

        body = provider._request.call_args_list[1].kwargs["json"]
        assert body["content"]["media"]["altText"] == "Buchcover mit Titel"
        assert body["content"]["media"]["id"] == "urn:li:image:1"

    def test_image_post_without_alt_text_omits_the_field(self):
        provider = LinkedInProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(
            side_effect=[
                _resp({"value": {"uploadUrl": "https://upload", "image": "urn:li:image:1"}}),
                MagicMock(headers={"x-restli-id": "urn:li:share:1"}),
            ]
        )
        provider._upload_binary = MagicMock()
        content = PublishContent(post_type=PostType.IMAGE, media_files=["/tmp/1.png"], media_urls=["https://cdn/1.png"])

        provider._publish_image_post("token", "urn:li:person:1", content)

        assert "altText" not in provider._request.call_args_list[1].kwargs["json"]["content"]["media"]


# ---------------------------------------------------------------------------
# Mastodon
# ---------------------------------------------------------------------------


class TestMastodonAltText:
    def _provider(self):
        provider = MastodonProvider(
            {"instance_url": "https://mastodon.example", "client_id": "id", "client_secret": "secret"}
        )
        return provider

    def test_each_attachment_is_uploaded_with_its_own_description(self, tmp_path):
        provider = self._provider()
        files = []
        for index in range(3):
            path = tmp_path / f"{index}.png"
            path.write_bytes(b"png")
            files.append(str(path))

        provider.get_instance_max_alt_text_length = MagicMock(return_value=1500)
        provider._request = MagicMock(
            side_effect=[
                _resp({"id": "media-0"}),
                _resp({"id": "media-1"}),
                _resp({"id": "media-2"}),
                _resp({"id": "status-1", "url": "https://mastodon.example/@a/1"}),
            ]
        )
        content = PublishContent(post_type=PostType.IMAGE, media_files=files, media_alt_texts=SLIDES)

        provider.publish_post("token", content)

        uploads = [call.kwargs["data"] for call in provider._request.call_args_list[:3]]
        assert [payload["description"] for payload in uploads] == SLIDES

    def test_attachment_without_description_uploads_without_the_field(self, tmp_path):
        provider = self._provider()
        path = tmp_path / "a.png"
        path.write_bytes(b"png")

        provider.get_instance_max_alt_text_length = MagicMock(return_value=1500)
        provider._request = MagicMock(
            side_effect=[_resp({"id": "media-0"}), _resp({"id": "status-1", "url": "https://x/1"})]
        )
        content = PublishContent(post_type=PostType.IMAGE, media_files=[str(path)])

        provider.publish_post("token", content)

        assert provider._request.call_args_list[0].kwargs["data"] is None

    def test_instance_limit_is_read_from_the_instance_configuration(self):
        provider = self._provider()
        provider._request = MagicMock(
            return_value=_resp({"configuration": {"media_attachments": {"description_limit": 10000}}})
        )

        assert provider.get_instance_max_alt_text_length("token") == 10000

    def test_instance_limit_falls_back_when_the_field_is_absent(self):
        provider = self._provider()
        provider._request = MagicMock(return_value=_resp({"configuration": {}}))

        assert provider.get_instance_max_alt_text_length("token") == DEFAULT_MAX_ALT_TEXT_LENGTH

    def test_instance_limit_falls_back_when_the_request_fails(self):
        provider = self._provider()
        provider._request = MagicMock(side_effect=RuntimeError("offline"))

        assert provider.get_instance_max_alt_text_length("token") == DEFAULT_MAX_ALT_TEXT_LENGTH


# ---------------------------------------------------------------------------
# Pinterest
# ---------------------------------------------------------------------------


class TestPinterestAltText:
    def test_falls_back_to_the_first_attachments_description(self):
        provider = PinterestProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(return_value=_resp({"id": "pin-1"}))
        content = PublishContent(
            post_type=PostType.PIN,
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["Ein Stapel Bücher"],
            extra={"board_id": "board-1"},
        )

        provider.publish_post("token", content)

        assert provider._request.call_args.kwargs["json"]["alt_text"] == "Ein Stapel Bücher"

    def test_explicit_per_account_value_wins(self):
        # The composer offers a dedicated Pinterest alt text field; when it is
        # filled it must beat the attachment's description.
        provider = PinterestProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(return_value=_resp({"id": "pin-1"}))
        content = PublishContent(
            post_type=PostType.PIN,
            media_urls=["https://cdn/1.png"],
            media_alt_texts=["Aus der Mediathek"],
            extra={"board_id": "board-1", "alt_text": "Im Composer eingetippt"},
        )

        provider.publish_post("token", content)

        assert provider._request.call_args.kwargs["json"]["alt_text"] == "Im Composer eingetippt"

    def test_without_any_alt_text_the_field_is_omitted(self):
        provider = PinterestProvider({"client_id": "id", "client_secret": "secret"})
        provider._request = MagicMock(return_value=_resp({"id": "pin-1"}))
        content = PublishContent(
            post_type=PostType.PIN,
            media_urls=["https://cdn/1.png"],
            extra={"board_id": "board-1"},
        )

        provider.publish_post("token", content)

        assert "alt_text" not in provider._request.call_args.kwargs["json"]
