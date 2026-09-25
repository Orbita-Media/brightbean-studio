"""Engine: a Google Business post gets the v4 parent of its connected location
and its button link as ``PublishContent.link_url``."""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.publisher.test_video_cover import _MediaAssetStub
from apps.publisher.tests import _build_dispatch_mocks

PARENT = "accounts/113894727651234567890/locations/2832287425028066333"
LINK = "https://orbita-media.de/buecher/festland?utm_source=google"


@override_settings(APP_URL="https://social.example.com/")
class GoogleBusinessDispatchTest(SimpleTestCase):
    def _run(self, platform_extra):
        engine, platform_post, provider = _build_dispatch_mocks(
            platform="google_business", account_platform_id=PARENT, platform_extra=platform_extra
        )
        seen = {}

        def _publish(_token, content):
            seen["extra"] = dict(content.extra)
            seen["link_url"] = content.link_url
            return provider.publish_post.return_value

        provider.publish_post.side_effect = _publish
        with (
            patch("apps.publisher.engine.get_provider", return_value=provider),
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
            patch("apps.media_library.models.MediaAsset", _MediaAssetStub({})),
        ):
            engine._dispatch_to_provider(platform_post)
        return seen

    def test_location_path_comes_from_the_connected_account(self):
        self.assertEqual(self._run({})["extra"]["location_path"], PARENT)

    def test_button_and_link_reach_the_provider(self):
        seen = self._run({"gbp_cta": "SHOP", "link_url": LINK})
        self.assertEqual(seen["extra"]["gbp_cta"], "SHOP")
        self.assertEqual(seen["link_url"], LINK)
        self.assertNotIn("link_url", seen["extra"])
