"""Tests for Bluesky provider session handling."""

import base64
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from providers.bluesky import (
    MAX_ALT_TEXT_LENGTH,
    MAX_EMBED_IMAGES,
    MAX_GALLERY_IMAGES,
    BlueskyProvider,
    _access_jwt_expires_in,
)
from providers.exceptions import PublishError
from providers.types import PostType, PublishContent


def _make_jwt(payload: dict) -> str:
    """Build a JWT-shaped string (header.payload.signature) – signature is unchecked."""

    def encode(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{encode({'alg': 'HS256'})}.{encode(payload)}.signature"


class TestAccessJwtExpiresIn:
    def test_returns_positive_for_future_exp(self):
        future = int(time.time()) + 3600
        jwt = _make_jwt({"exp": future})
        result = _access_jwt_expires_in(jwt)
        assert result is not None
        assert 3595 <= result <= 3600

    def test_returns_zero_for_past_exp(self):
        past = int(time.time()) - 300
        jwt = _make_jwt({"exp": past})
        assert _access_jwt_expires_in(jwt) == 0

    def test_returns_none_for_malformed_jwt(self):
        assert _access_jwt_expires_in("not-a-jwt") is None
        assert _access_jwt_expires_in("only.two") is None
        assert _access_jwt_expires_in("a.!!!notbase64!!!.c") is None

    def test_returns_none_when_exp_missing(self):
        jwt = _make_jwt({"sub": "did:plc:abc"})
        assert _access_jwt_expires_in(jwt) is None

    def test_returns_none_when_exp_not_numeric(self):
        jwt = _make_jwt({"exp": "tomorrow"})
        assert _access_jwt_expires_in(jwt) is None


class TestCreateSession:
    @patch.object(BlueskyProvider, "_request")
    def test_populates_expires_in_from_jwt(self, mock_request):
        future = int(time.time()) + 7200
        access_jwt = _make_jwt({"exp": future})
        mock_request.return_value = MagicMock(
            json=MagicMock(return_value={"accessJwt": access_jwt, "refreshJwt": "refresh"}),
        )

        provider = BlueskyProvider()
        tokens = provider.create_session("user.bsky.social", "app-pw")

        assert tokens.access_token == access_jwt
        assert tokens.refresh_token == "refresh"
        assert tokens.expires_in is not None
        assert 7195 <= tokens.expires_in <= 7200


class TestRefreshToken:
    @patch.object(BlueskyProvider, "_request")
    def test_populates_expires_in_from_jwt(self, mock_request):
        future = int(time.time()) + 3600
        access_jwt = _make_jwt({"exp": future})
        mock_request.return_value = MagicMock(
            json=MagicMock(return_value={"accessJwt": access_jwt, "refreshJwt": "new-refresh"}),
        )

        provider = BlueskyProvider()
        tokens = provider.refresh_token("old-refresh")

        assert tokens.access_token == access_jwt
        assert tokens.refresh_token == "new-refresh"
        assert tokens.expires_in is not None
        assert 3595 <= tokens.expires_in <= 3600


class TestBuildEmbedAltText:
    """Each image in an app.bsky.embed.images embed carries its own alt text."""

    @staticmethod
    def _provider_with_blobs():
        provider = BlueskyProvider()
        provider._upload_blob = MagicMock(side_effect=lambda _token, path: {"$type": "blob", "ref": path})
        return provider

    def test_each_image_gets_its_own_alt_text_in_order(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png", "/tmp/2.png", "/tmp/3.png"],
            media_alt_texts=["Folie 1", "Folie 2", "Folie 3"],
        )

        embed = provider._build_embed("token", content)

        assert embed["$type"] == "app.bsky.embed.images"
        assert [image["alt"] for image in embed["images"]] == ["Folie 1", "Folie 2", "Folie 3"]
        # The blob ref proves alt text and image stayed on the same slide.
        assert [image["image"]["ref"] for image in embed["images"]] == ["/tmp/1.png", "/tmp/2.png", "/tmp/3.png"]

    def test_missing_alt_text_publishes_empty_string_instead_of_failing(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png", "/tmp/2.png"],
            media_alt_texts=["Nur die erste"],
        )

        embed = provider._build_embed("token", content)

        assert [image["alt"] for image in embed["images"]] == ["Nur die erste", ""]

    def test_no_alt_texts_at_all_still_builds_a_valid_embed(self):
        provider = self._provider_with_blobs()
        content = PublishContent(post_type=PostType.IMAGE, media_files=["/tmp/1.png"])

        embed = provider._build_embed("token", content)

        assert embed["images"] == [{"alt": "", "image": {"$type": "blob", "ref": "/tmp/1.png"}}]

    def test_four_images_fill_the_images_embed(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(MAX_EMBED_IMAGES)],
            media_alt_texts=[f"Folie {i}" for i in range(MAX_EMBED_IMAGES)],
        )

        embed = provider._build_embed("token", content)

        assert embed["$type"] == "app.bsky.embed.images"
        assert len(embed["images"]) == MAX_EMBED_IMAGES
        assert [image["alt"] for image in embed["images"]] == ["Folie 0", "Folie 1", "Folie 2", "Folie 3"]

    def test_truncates_overlong_alt_text(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png"],
            media_alt_texts=["ü" * (MAX_ALT_TEXT_LENGTH + 500)],
        )

        embed = provider._build_embed("token", content)

        assert len(embed["images"][0]["alt"]) == MAX_ALT_TEXT_LENGTH

    def test_video_embed_carries_alt_text(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.VIDEO,
            media_files=["/tmp/clip.mp4"],
            media_alt_texts=["Kurzes Erklärvideo"],
        )

        embed = provider._build_embed("token", content)

        assert embed["$type"] == "app.bsky.embed.video"
        assert embed["alt"] == "Kurzes Erklärvideo"

    def test_video_embed_without_alt_text_omits_the_field(self):
        provider = self._provider_with_blobs()
        content = PublishContent(post_type=PostType.VIDEO, media_files=["/tmp/clip.mp4"])

        embed = provider._build_embed("token", content)

        assert "alt" not in embed

    def test_falls_back_to_legacy_single_alt_text_key(self):
        # Older callers only set extra["alt_text"]; keep honouring it.
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png", "/tmp/2.png"],
            extra={"alt_text": "Gilt für alle"},
        )

        embed = provider._build_embed("token", content)

        assert [image["alt"] for image in embed["images"]] == ["Gilt für alle", "Gilt für alle"]


def _png(pfad, breite: int, hoehe: int) -> str:
    """Write a real PNG so the aspect ratio can be measured, not mocked."""
    from PIL import Image

    Image.new("RGB", (breite, hoehe), (30, 30, 40)).save(pfad)
    return str(pfad)


class TestGalleryEmbed:
    """From the fifth slide on, app.bsky.embed.gallery carries the whole post.

    Until 05.08.2026 slide five started a reply to our own post. It does not
    any more: the gallery embed holds up to ten images in ONE record.
    """

    @staticmethod
    def _provider():
        provider = BlueskyProvider()
        provider._upload_blob = MagicMock(side_effect=lambda _token, path: {"$type": "blob", "ref": path})
        return provider

    @staticmethod
    def _content(tmp_path, anzahl: int, breite: int = 1080, hoehe: int = 1350) -> PublishContent:
        return PublishContent(
            post_type=PostType.IMAGE,
            media_files=[_png(tmp_path / f"{i}.png", breite, hoehe) for i in range(1, anzahl + 1)],
            media_alt_texts=[f"Folie {i}" for i in range(1, anzahl + 1)],
        )

    def test_four_slides_stay_on_the_long_standing_images_embed(self, tmp_path):
        # Below five nothing changes: every client has understood this embed
        # for years, and the official app renders it exactly as before.
        embed = self._provider()._build_embed("token", self._content(tmp_path, 4))

        assert embed["$type"] == "app.bsky.embed.images"
        assert len(embed["images"]) == 4

    def test_five_slides_switch_to_the_gallery_embed(self, tmp_path):
        embed = self._provider()._build_embed("token", self._content(tmp_path, 5))

        assert embed["$type"] == "app.bsky.embed.gallery"
        assert len(embed["items"]) == 5

    def test_the_whole_six_slide_carousel_fits_into_one_embed(self, tmp_path):
        # The case that started all of this.
        embed = self._provider()._build_embed("token", self._content(tmp_path, 6))

        assert len(embed["items"]) == 6
        assert [item["alt"] for item in embed["items"]] == [f"Folie {i}" for i in range(1, 7)]

    def test_ten_slides_still_fit(self, tmp_path):
        embed = self._provider()._build_embed("token", self._content(tmp_path, MAX_GALLERY_IMAGES))

        assert len(embed["items"]) == MAX_GALLERY_IMAGES

    def test_every_gallery_item_carries_the_measured_aspect_ratio(self, tmp_path):
        # aspectRatio is REQUIRED on gallery#image, unlike images#image.
        embed = self._provider()._build_embed("token", self._content(tmp_path, 5, breite=1080, hoehe=1350))

        assert all(item["aspectRatio"] == {"width": 1080, "height": 1350} for item in embed["items"])

    def test_aspect_ratio_is_read_per_file_not_from_the_first(self, tmp_path):
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[
                _png(tmp_path / "hoch.png", 1080, 1350),
                _png(tmp_path / "quer.png", 1200, 630),
                _png(tmp_path / "quadrat.png", 1080, 1080),
                _png(tmp_path / "story.png", 1080, 1920),
                _png(tmp_path / "pin.png", 1000, 1500),
            ],
        )

        embed = self._provider()._build_embed("token", content)

        assert [item["aspectRatio"] for item in embed["items"]] == [
            {"width": 1080, "height": 1350},
            {"width": 1200, "height": 630},
            {"width": 1080, "height": 1080},
            {"width": 1080, "height": 1920},
            {"width": 1000, "height": 1500},
        ]

    def test_images_embed_carries_no_aspect_ratio(self, tmp_path):
        # Optional there, and we have never sent it – no silent format change.
        embed = self._provider()._build_embed("token", self._content(tmp_path, 3))

        assert all("aspectRatio" not in image for image in embed["images"])

    def test_alt_texts_stay_on_their_own_slide(self, tmp_path):
        content = self._content(tmp_path, 6)
        content.media_alt_texts = ["Folie 1", "", "Folie 3", "Folie 4", "", "Folie 6"]

        embed = self._provider()._build_embed("token", content)

        # A gap stays a gap. A shifted description is worse than none.
        assert [item["alt"] for item in embed["items"]] == ["Folie 1", "", "Folie 3", "Folie 4", "", "Folie 6"]

    def test_unreadable_file_fails_instead_of_posting_without_dimensions(self, tmp_path):
        kaputt = tmp_path / "kaputt.png"
        kaputt.write_bytes(b"kein PNG")
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[_png(tmp_path / f"{i}.png", 1080, 1350) for i in range(4)] + [str(kaputt)],
        )

        with pytest.raises(PublishError, match="Bildmasse"):
            self._provider()._build_embed("token", content)

    def test_no_blob_is_uploaded_when_a_dimension_cannot_be_read(self, tmp_path):
        # A gallery post that cannot be finished should not cost an upload.
        kaputt = tmp_path / "kaputt.png"
        kaputt.write_bytes(b"kein PNG")
        provider = self._provider()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[_png(tmp_path / f"{i}.png", 1080, 1350) for i in range(4)] + [str(kaputt)],
        )

        with pytest.raises(PublishError):
            provider._build_embed("token", content)

        provider._upload_blob.assert_not_called()

    def test_text_only_post_has_no_embed(self):
        assert self._provider()._build_embed("token", PublishContent(text="nur Text")) is None

    def test_video_ignores_further_attachments(self):
        provider = self._provider()
        content = PublishContent(post_type=PostType.VIDEO, media_files=["/tmp/clip.mp4", "/tmp/zweit.mp4"])

        embed = provider._build_embed("token", content)

        assert embed["$type"] == "app.bsky.embed.video"
        assert provider._upload_blob.call_count == 1


class TestPublishPostIsAlwaysOneRecord:
    """One post, always. The reply chain is gone."""

    @staticmethod
    def _provider(records: list[dict]):
        """Provider whose HTTP layer records every createRecord payload."""
        provider = BlueskyProvider()
        provider._upload_blob = MagicMock(side_effect=lambda _token, path: {"$type": "blob", "ref": path})

        def fake_request(method, url, **kwargs):
            if url.endswith("com.atproto.server.getSession"):
                return MagicMock(
                    json=MagicMock(return_value={"did": "did:plc:abc", "handle": "orbitamedia.bsky.social"})
                )
            if url.endswith("com.atproto.repo.createRecord"):
                records.append(kwargs["json"]["record"])
                return MagicMock(
                    json=MagicMock(
                        return_value={
                            "uri": "at://did:plc:abc/app.bsky.feed.post/rkey1",
                            "cid": "cid1",
                        }
                    )
                )
            raise AssertionError(f"unerwarteter Aufruf: {url}")

        provider._request = MagicMock(side_effect=fake_request)
        return provider

    @staticmethod
    def _carousel(tmp_path, anzahl=6) -> PublishContent:
        return PublishContent(
            text="Sechs Folien, ein Beitrag.",
            post_type=PostType.IMAGE,
            media_files=[_png(tmp_path / f"{i}.png", 1080, 1350) for i in range(1, anzahl + 1)],
            media_alt_texts=[f"Folie {i}" for i in range(1, anzahl + 1)],
        )

    def test_six_slides_produce_exactly_one_record(self, tmp_path):
        records: list[dict] = []

        self._provider(records).publish_post("token", self._carousel(tmp_path))

        assert len(records) == 1
        assert len(records[0]["embed"]["items"]) == 6

    def test_no_record_is_a_reply(self, tmp_path):
        records: list[dict] = []

        self._provider(records).publish_post("token", self._carousel(tmp_path))

        assert all("reply" not in record for record in records)

    def test_the_caption_stays_on_the_one_post(self, tmp_path):
        records: list[dict] = []

        self._provider(records).publish_post("token", self._carousel(tmp_path))

        assert records[0]["text"] == "Sechs Folien, ein Beitrag."

    def test_result_carries_no_thread_metadata(self, tmp_path):
        records: list[dict] = []

        result = self._provider(records).publish_post("token", self._carousel(tmp_path))

        assert result.platform_post_id == "at://did:plc:abc/app.bsky.feed.post/rkey1"
        assert result.url == "https://bsky.app/profile/orbitamedia.bsky.social/post/rkey1"
        assert "thread" not in result.extra
        assert "publish_warning" not in result.extra

    def test_ten_slides_are_still_one_post(self, tmp_path):
        records: list[dict] = []

        self._provider(records).publish_post("token", self._carousel(tmp_path, anzahl=MAX_GALLERY_IMAGES))

        assert len(records) == 1

    def test_eleven_slides_fail_before_anything_is_posted(self, tmp_path):
        records: list[dict] = []
        provider = self._provider(records)

        with pytest.raises(PublishError, match="höchstens 10 Bilder"):
            provider.publish_post("token", self._carousel(tmp_path, anzahl=MAX_GALLERY_IMAGES + 1))

        assert records == []
        provider._upload_blob.assert_not_called()

    def test_the_overflow_error_is_permanent(self, tmp_path):
        # Retrying sends the same attachments; the fix belongs upstream.
        provider = self._provider([])

        with pytest.raises(PublishError) as fehler:
            provider.publish_post("token", self._carousel(tmp_path, anzahl=12))

        assert fehler.value.retryable is False

    def test_blobs_are_uploaded_before_the_record_is_written(self, tmp_path):
        records: list[dict] = []
        provider = self._provider(records)
        reihenfolge: list[str] = []

        provider._upload_blob = MagicMock(
            side_effect=lambda _t, path: (reihenfolge.append(f"blob {path}"), {"$type": "blob", "ref": path})[1]
        )
        echt = provider._create_post_record
        provider._create_post_record = lambda *a, **kw: (reihenfolge.append("record"), echt(*a, **kw))[1]

        provider.publish_post("token", self._carousel(tmp_path))

        letzter_blob = max(i for i, s in enumerate(reihenfolge) if s.startswith("blob"))
        assert reihenfolge.index("record") > letzter_blob

    def test_a_failing_record_raises(self, tmp_path):
        provider = self._provider([])
        provider._create_post_record = MagicMock(side_effect=RuntimeError("PDS weg"))

        with pytest.raises(RuntimeError):
            provider.publish_post("token", self._carousel(tmp_path))


