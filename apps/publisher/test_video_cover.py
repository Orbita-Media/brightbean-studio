"""Engine: the Titelbild (``thumbnail_asset_id``) reaches each provider in the form it needs.

* Instagram fetches the image itself → public ABSOLUTE URL as ``thumbnail_url``
  (same APP_URL rule as the media), no temp file,
* YouTube / Facebook upload it → temp file as ``thumbnail_file``, cleaned up
  after the publish,
* ``thumb_offset_ms`` passes through untouched,
* a missing asset or a malformed id never stops the publish,
* regression: a Pinterest cover without a thumbnail no longer hits a
  NameError (the MediaAsset import used to live in the thumbnail branch).
"""

import os
import uuid
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from apps.publisher.tests import _build_dispatch_mocks, _FakeStoredFile


def _fake_asset(url: str, filename: str = "cover.jpg", data: bytes = b"\xff\xd8cover"):
    asset = MagicMock()
    asset.file = _FakeStoredFile(url, data)
    asset.filename = filename
    return asset


class _MediaAssetStub:
    """Stand-in for the MediaAsset model: ``objects.get`` + ``DoesNotExist``."""

    class DoesNotExist(Exception):  # noqa: N818 - mirrors the Django model API
        pass

    def __init__(self, assets: dict):
        self._assets = assets
        self.objects = MagicMock()
        self.objects.get.side_effect = self._get

    def _get(self, id):  # noqa: A002 - mirrors the ORM keyword
        if id == "not-a-uuid":
            raise ValidationError("bad uuid")
        try:
            return self._assets[str(id)]
        except KeyError:
            raise self.DoesNotExist from None


@override_settings(APP_URL="https://social.example.com/")
class DispatchCoverTest(SimpleTestCase):
    def _run(self, platform, platform_extra, assets, capture=None):
        engine, platform_post, provider = _build_dispatch_mocks(
            platform=platform,
            account_platform_id="acc-1",
            platform_extra=platform_extra,
        )
        seen = {}

        def _publish(_token, content):
            seen["extra"] = dict(content.extra)
            path = content.extra.get("thumbnail_file")
            if path:
                seen["file_exists"] = os.path.exists(path)
                with open(path, "rb") as fh:
                    seen["file_bytes"] = fh.read()
            return provider.publish_post.return_value

        provider.publish_post.side_effect = _publish
        stub = _MediaAssetStub(assets)
        with (
            patch("apps.publisher.engine.get_provider", return_value=provider),
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
            patch("apps.media_library.models.MediaAsset", stub),
        ):
            engine._dispatch_to_provider(platform_post)
        return seen

    def test_instagram_gets_an_absolute_public_url(self):
        aid = str(uuid.uuid4())
        seen = self._run(
            "instagram",
            {"thumbnail_asset_id": aid, "thumb_offset_ms": 1200},
            {aid: _fake_asset("/media/workspace/cover.jpg")},
        )
        extra = seen["extra"]
        self.assertEqual(extra["thumbnail_url"], "https://social.example.com/media/workspace/cover.jpg")
        self.assertNotIn("thumbnail_file", extra)
        self.assertNotIn("thumbnail_asset_id", extra)
        self.assertEqual(extra["thumb_offset_ms"], 1200)

    def test_instagram_login_keeps_a_storage_url_as_is(self):
        aid = str(uuid.uuid4())
        seen = self._run(
            "instagram_login",
            {"thumbnail_asset_id": aid},
            {aid: _fake_asset("https://r2.example.com/signed/cover.jpg?sig=1")},
        )
        self.assertEqual(seen["extra"]["thumbnail_url"], "https://r2.example.com/signed/cover.jpg?sig=1")

    def test_facebook_and_youtube_get_a_temp_file_that_is_removed_afterwards(self):
        for platform in ("facebook", "youtube"):
            aid = str(uuid.uuid4())
            seen = self._run(
                platform,
                {"thumbnail_asset_id": aid},
                {aid: _fake_asset("/media/c.jpg", data=b"jpeg-bytes")},
            )
            extra = seen["extra"]
            self.assertTrue(seen["file_exists"], platform)
            self.assertEqual(seen["file_bytes"], b"jpeg-bytes")
            self.assertTrue(extra["thumbnail_file"].endswith(".jpg"))
            self.assertNotIn("thumbnail_url", extra)
            self.assertFalse(os.path.exists(extra["thumbnail_file"]), "temp file must be cleaned up")

    def test_tiktok_offset_passes_through(self):
        seen = self._run("tiktok", {"thumb_offset_ms": 800, "video_cover_timestamp_ms": 800}, {})
        self.assertEqual(seen["extra"]["thumb_offset_ms"], 800)
        self.assertEqual(seen["extra"]["video_cover_timestamp_ms"], 800)

    def test_missing_or_malformed_asset_does_not_stop_the_publish(self):
        for bad in (str(uuid.uuid4()), "not-a-uuid"):
            with self.assertLogs("apps.publisher.engine", level="WARNING"):
                seen = self._run("instagram", {"thumbnail_asset_id": bad}, {})
            self.assertNotIn("thumbnail_url", seen["extra"])
            self.assertNotIn("thumbnail_asset_id", seen["extra"])

    def test_pinterest_cover_without_thumbnail_is_resolved(self):
        cid = str(uuid.uuid4())
        seen = self._run("pinterest", {"cover_image_asset_id": cid}, {cid: _fake_asset("/media/pin.png", "pin.png")})
        self.assertTrue(seen["extra"]["cover_image_file"].endswith(".png"))
