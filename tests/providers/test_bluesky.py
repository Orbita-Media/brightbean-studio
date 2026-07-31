"""Tests for Bluesky provider session handling."""

import base64
import json
import time
from unittest.mock import MagicMock, patch

import pytest

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

        embed = provider._build_embeds("token", content)[0]

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

        embed = provider._build_embeds("token", content)[0]

        assert [image["alt"] for image in embed["images"]] == ["Nur die erste", ""]

    def test_no_alt_texts_at_all_still_builds_a_valid_embed(self):
        provider = self._provider_with_blobs()
        content = PublishContent(post_type=PostType.IMAGE, media_files=["/tmp/1.png"])

        embed = provider._build_embeds("token", content)[0]

        assert embed["images"] == [{"alt": "", "image": {"$type": "blob", "ref": "/tmp/1.png"}}]

    def test_first_post_carries_at_most_four_images(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(6)],
            media_alt_texts=[f"Folie {i}" for i in range(6)],
        )

        embed = provider._build_embeds("token", content)[0]

        assert len(embed["images"]) == MAX_EMBED_IMAGES
        assert [image["alt"] for image in embed["images"]] == ["Folie 0", "Folie 1", "Folie 2", "Folie 3"]

    def test_truncates_overlong_alt_text(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png"],
            media_alt_texts=["ü" * (MAX_ALT_TEXT_LENGTH + 500)],
        )

        embed = provider._build_embeds("token", content)[0]

        assert len(embed["images"][0]["alt"]) == MAX_ALT_TEXT_LENGTH

    def test_video_embed_carries_alt_text(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.VIDEO,
            media_files=["/tmp/clip.mp4"],
            media_alt_texts=["Kurzes Erklärvideo"],
        )

        embed = provider._build_embeds("token", content)[0]

        assert embed["$type"] == "app.bsky.embed.video"
        assert embed["alt"] == "Kurzes Erklärvideo"

    def test_video_embed_without_alt_text_omits_the_field(self):
        provider = self._provider_with_blobs()
        content = PublishContent(post_type=PostType.VIDEO, media_files=["/tmp/clip.mp4"])

        embed = provider._build_embeds("token", content)[0]

        assert "alt" not in embed

    def test_falls_back_to_legacy_single_alt_text_key(self):
        # Older callers only set extra["alt_text"]; keep honouring it.
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=["/tmp/1.png", "/tmp/2.png"],
            extra={"alt_text": "Gilt für alle"},
        )

        embed = provider._build_embeds("token", content)[0]

        assert [image["alt"] for image in embed["images"]] == ["Gilt für alle", "Gilt für alle"]


class TestBuildEmbedsChunking:
    """More than four images become several embeds; alt texts stay in place."""

    @staticmethod
    def _provider_with_blobs():
        provider = BlueskyProvider()
        provider._upload_blob = MagicMock(side_effect=lambda _token, path: {"$type": "blob", "ref": path})
        return provider

    def test_six_images_become_two_embeds_of_four_and_two(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, 7)],
            media_alt_texts=[f"Folie {i}" for i in range(1, 7)],
        )

        embeds = provider._build_embeds("token", content)

        assert len(embeds) == 2
        assert [len(e["images"]) for e in embeds] == [4, 2]

    def test_alt_texts_do_not_shift_across_the_chunk_boundary(self):
        # The actual trap: the index must NOT restart per group, otherwise
        # slide 5 would carry the description of slide 1.
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, 7)],
            media_alt_texts=[f"Folie {i}" for i in range(1, 7)],
        )

        embeds = provider._build_embeds("token", content)

        assert [i["alt"] for i in embeds[1]["images"]] == ["Folie 5", "Folie 6"]
        assert [i["image"]["ref"] for i in embeds[1]["images"]] == ["/tmp/5.png", "/tmp/6.png"]

    def test_every_image_keeps_its_own_alt_text_across_the_whole_chain(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, 10)],
            media_alt_texts=[f"Folie {i}" for i in range(1, 10)],
        )

        embeds = provider._build_embeds("token", content)
        paare = [(i["image"]["ref"], i["alt"]) for e in embeds for i in e["images"]]

        assert len(embeds) == 3
        assert paare == [(f"/tmp/{i}.png", f"Folie {i}") for i in range(1, 10)]

    def test_gap_in_the_alt_texts_does_not_shift_the_rest(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, 7)],
            media_alt_texts=["Folie 1", "", "Folie 3", "Folie 4", "", "Folie 6"],
        )

        embeds = provider._build_embeds("token", content)
        alts = [i["alt"] for e in embeds for i in e["images"]]

        assert alts == ["Folie 1", "", "Folie 3", "Folie 4", "", "Folie 6"]

    def test_exactly_four_images_stay_one_post(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, 5)],
        )

        assert len(provider._build_embeds("token", content)) == 1

    def test_text_only_post_has_no_embed(self):
        provider = self._provider_with_blobs()

        assert provider._build_embeds("token", PublishContent(text="nur Text")) == []

    def test_video_stays_a_single_embed(self):
        provider = self._provider_with_blobs()
        content = PublishContent(
            post_type=PostType.VIDEO,
            media_files=["/tmp/clip.mp4", "/tmp/zweit.mp4"],
        )

        embeds = provider._build_embeds("token", content)

        assert len(embeds) == 1
        assert embeds[0]["$type"] == "app.bsky.embed.video"


class TestPublishPostThread:
    """publish_post writes a reply chain when the carousel exceeds four slides."""

    @staticmethod
    def _provider(records: list[dict]):
        """Provider whose HTTP layer records every createRecord payload."""
        provider = BlueskyProvider()
        provider._upload_blob = MagicMock(side_effect=lambda _token, path: {"$type": "blob", "ref": path})

        counter = {"n": 0}

        def fake_request(method, url, **kwargs):
            if url.endswith("com.atproto.server.getSession"):
                return MagicMock(
                    json=MagicMock(return_value={"did": "did:plc:abc", "handle": "orbitamedia.bsky.social"})
                )
            if url.endswith("com.atproto.repo.createRecord"):
                records.append(kwargs["json"]["record"])
                counter["n"] += 1
                n = counter["n"]
                return MagicMock(
                    json=MagicMock(
                        return_value={
                            "uri": f"at://did:plc:abc/app.bsky.feed.post/rkey{n}",
                            "cid": f"cid{n}",
                        }
                    )
                )
            raise AssertionError(f"unerwarteter Aufruf: {url}")

        provider._request = MagicMock(side_effect=fake_request)
        return provider

    @staticmethod
    def _carousel(count=6):
        return PublishContent(
            text="Sechs Folien, vier passen rein.",
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, count + 1)],
            media_alt_texts=[f"Folie {i}" for i in range(1, count + 1)],
        )

    def test_six_slides_produce_two_records(self):
        records: list[dict] = []
        provider = self._provider(records)

        provider.publish_post("token", self._carousel())

        assert len(records) == 2
        assert [len(r["embed"]["images"]) for r in records] == [4, 2]

    def test_reply_points_at_root_and_parent(self):
        records: list[dict] = []
        provider = self._provider(records)

        provider.publish_post("token", self._carousel())

        assert "reply" not in records[0]
        reply = records[1]["reply"]
        assert reply["root"] == {"uri": "at://did:plc:abc/app.bsky.feed.post/rkey1", "cid": "cid1"}
        assert reply["parent"] == {"uri": "at://did:plc:abc/app.bsky.feed.post/rkey1", "cid": "cid1"}

    def test_third_post_replies_to_the_second_but_roots_at_the_first(self):
        records: list[dict] = []
        provider = self._provider(records)

        provider.publish_post("token", self._carousel(count=9))

        assert len(records) == 3
        assert records[2]["reply"]["root"]["cid"] == "cid1"
        assert records[2]["reply"]["parent"]["cid"] == "cid2"

    def test_alt_texts_survive_the_split(self):
        records: list[dict] = []
        provider = self._provider(records)

        provider.publish_post("token", self._carousel())

        alle = [img["alt"] for r in records for img in r["embed"]["images"]]
        assert alle == [f"Folie {i}" for i in range(1, 7)]

    def test_root_text_is_the_caption_and_the_reply_gets_a_short_hint(self):
        records: list[dict] = []
        provider = self._provider(records)

        provider.publish_post("token", self._carousel())

        assert records[0]["text"] == "Sechs Folien, vier passen rein."
        assert records[1]["text"] == "Fortsetzung – Bild 5 bis 6 von 6"
        assert len(records[1]["text"]) <= provider.max_caption_length

    def test_single_overflow_image_gets_the_singular_wording(self):
        records: list[dict] = []
        provider = self._provider(records)

        provider.publish_post("token", self._carousel(count=5))

        assert records[1]["text"] == "Fortsetzung – Bild 5 von 5"

    def test_continuation_text_can_be_overridden(self):
        records: list[dict] = []
        provider = self._provider(records)
        content = self._carousel()
        content.extra["thread_continuation_text"] = "Weiter geht es ({start}/{total})"

        provider.publish_post("token", content)

        assert records[1]["text"] == "Weiter geht es (5/6)"

    def test_overlong_continuation_text_is_cut_to_the_limit(self):
        records: list[dict] = []
        provider = self._provider(records)
        content = self._carousel()
        content.extra["thread_continuation_text"] = "ü" * 500

        provider.publish_post("token", content)

        assert len(records[1]["text"]) == provider.max_caption_length

    def test_result_reports_the_root_post_and_the_chain(self):
        records: list[dict] = []
        provider = self._provider(records)

        result = provider.publish_post("token", self._carousel())

        assert result.platform_post_id == "at://did:plc:abc/app.bsky.feed.post/rkey1"
        assert result.url == "https://bsky.app/profile/orbitamedia.bsky.social/post/rkey1"
        assert result.extra["thread"]["expected_posts"] == 2
        assert result.extra["thread"]["image_count"] == 6
        assert result.extra["thread"]["incomplete"] is False
        assert [p["image_count"] for p in result.extra["thread"]["posts"]] == [4, 2]

    def test_four_slides_stay_a_plain_post_without_thread_metadata(self):
        records: list[dict] = []
        provider = self._provider(records)

        result = provider.publish_post("token", self._carousel(count=4))

        assert len(records) == 1
        assert "thread" not in result.extra

    def test_blobs_are_uploaded_before_the_first_record_is_written(self):
        # Otherwise half a thread hangs in the timeline that no retry can
        # repair without posting the whole carousel a second time.
        records: list[dict] = []
        provider = self._provider(records)
        reihenfolge: list[str] = []

        provider._upload_blob = MagicMock(
            side_effect=lambda _t, path: (reihenfolge.append(f"blob {path}"), {"$type": "blob", "ref": path})[1]
        )
        echt = provider._create_post_record
        provider._create_post_record = lambda *a, **kw: (reihenfolge.append("record"), echt(*a, **kw))[1]

        provider.publish_post("token", self._carousel())

        letzter_blob = max(i for i, s in enumerate(reihenfolge) if s.startswith("blob"))
        assert reihenfolge.index("record") > letzter_blob

    def test_failing_reply_keeps_the_root_and_reports_the_gap(self):
        records: list[dict] = []
        provider = self._provider(records)
        echt = provider._create_post_record
        rufe = {"n": 0}

        def flaky(*args, **kwargs):
            rufe["n"] += 1
            if rufe["n"] > 1:
                raise RuntimeError("PDS weg")
            return echt(*args, **kwargs)

        provider._create_post_record = flaky

        result = provider.publish_post("token", self._carousel())

        # No exception outwards: the publisher would otherwise retry and post
        # the whole carousel a second time.
        assert result.platform_post_id.endswith("rkey1")
        assert result.extra["thread"]["incomplete"] is True
        assert "unvollständig" in result.extra["publish_warning"]

    def test_failing_root_still_raises(self):
        records: list[dict] = []
        provider = self._provider(records)
        provider._create_post_record = MagicMock(side_effect=RuntimeError("PDS weg"))

        with pytest.raises(RuntimeError):
            provider.publish_post("token", self._carousel())
