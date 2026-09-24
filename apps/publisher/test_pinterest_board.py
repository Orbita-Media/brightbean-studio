"""Engine: a pin without its own board goes to the account's default board,
and a video-pin cover reaches Pinterest as a public URL."""

import uuid
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.publisher.test_video_cover import _fake_asset, _MediaAssetStub
from apps.publisher.tests import _build_dispatch_mocks


@override_settings(APP_URL="https://social.example.com/")
class PinterestDispatchTest(SimpleTestCase):
    def _run(self, platform_extra, *, settings=None, assets=None):
        engine, platform_post, provider = _build_dispatch_mocks(
            platform="pinterest", account_platform_id="pin-acc", platform_extra=platform_extra
        )
        platform_post.social_account.platform_settings = settings or {}
        seen = {}

        def _publish(_token, content):
            seen["extra"] = dict(content.extra)
            return provider.publish_post.return_value

        provider.publish_post.side_effect = _publish
        with (
            patch("apps.publisher.engine.get_provider", return_value=provider),
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
            patch("apps.media_library.models.MediaAsset", _MediaAssetStub(assets or {})),
        ):
            engine._dispatch_to_provider(platform_post)
        return seen["extra"]

    def test_default_board_fills_a_pin_without_board(self):
        extra = self._run({}, settings={"pinterest_default_board_id": "1082834372847314271"})
        self.assertEqual(extra["board_id"], "1082834372847314271")

    def test_own_board_wins_over_the_default(self):
        extra = self._run({"board_id": "42"}, settings={"pinterest_default_board_id": "1082834372847314271"})
        self.assertEqual(extra["board_id"], "42")

    def test_no_board_anywhere_stays_empty_for_the_provider_to_refuse(self):
        extra = self._run({})
        self.assertFalse(extra.get("board_id"))

    def test_cover_image_becomes_a_public_url_and_a_file(self):
        cid = str(uuid.uuid4())
        extra = self._run(
            {"board_id": "42", "cover_image_asset_id": cid},
            assets={cid: _fake_asset("/media/pin-cover.jpg", "pin-cover.jpg")},
        )
        self.assertEqual(extra["cover_image_url"], "https://social.example.com/media/pin-cover.jpg")
        self.assertTrue(extra["cover_image_file"].endswith(".jpg"))
        self.assertNotIn("cover_image_asset_id", extra)
