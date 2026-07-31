"""Tests for Bluesky provider session handling."""

import base64
import json
import time
from unittest.mock import MagicMock, patch

from providers.bluesky import MAX_ALT_TEXT_LENGTH, MAX_EMBED_IMAGES, BlueskyProvider, _access_jwt_expires_in
from providers.types import PostType, PublishContent


def _make_jwt(payload: dict) -> str:
    """Build a JWT-shaped string (header.payload.signature) — signature is unchecked."""

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

    def test_caps_at_four_images(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(6)],
            media_alt_texts=[f"Folie {i}" for i in range(6)],
        )

        embed = provider._build_embed("token", content)

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
