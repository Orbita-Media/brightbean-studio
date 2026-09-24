"""Engine: a story reaches the provider as ``PostType.STORY`` – and gets no first comment.

* ``platform_extra["post_type"] = "story"`` wins over the media-based guess
  (a lone video would otherwise be a reel, a lone image a feed post),
* the provider learns the real media type per item (``media_types``), so a
  presigned URL without a file extension cannot turn a video into an image,
* a published story does not get the post's first comment (a story has no
  comments; the comment belongs to the feed post it echoes).
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import SimpleTestCase

from apps.publisher.engine import PublishEngine
from apps.publisher.tests import _build_dispatch_mocks, _fake_attachment
from providers.types import PostType


class ResolveStoryTypeTest(SimpleTestCase):
    def test_story_hint_beats_the_media_guess(self):
        for platform in ("instagram", "instagram_login", "facebook"):
            for media_type in ("video", "image"):
                got = PublishEngine._resolve_post_type(platform, {"post_type": "story"}, 1, media_type)
                self.assertEqual(got, PostType.STORY, (platform, media_type))

    def test_without_hint_a_lone_video_stays_a_video(self):
        self.assertEqual(PublishEngine._resolve_post_type("instagram", {}, 1, "video"), PostType.VIDEO)
        self.assertEqual(PublishEngine._resolve_post_type("facebook", {}, 1, "image"), PostType.IMAGE)


class DispatchStoryTest(SimpleTestCase):
    def test_provider_gets_story_type_and_media_types(self):
        video = _fake_attachment("reel", media_type="video")
        video.media_asset.duration = 54.0
        video.media_asset.file.url = "https://cdn.example.com/abc?X-Amz-Signature=1"
        engine, platform_post, provider = _build_dispatch_mocks(
            platform="facebook",
            account_platform_id="page-1",
            platform_extra={"post_type": "story"},
            attachments=[video],
        )
        seen = {}

        def _publish(_token, content):
            seen["content"] = content
            return provider.publish_post.return_value

        provider.publish_post.side_effect = _publish
        with (
            patch("apps.publisher.engine.get_provider", return_value=provider),
            patch("apps.publisher.engine._resolve_publish_credentials", return_value={}),
        ):
            engine._dispatch_to_provider(platform_post)

        content = seen["content"]
        self.assertEqual(content.post_type, PostType.STORY)
        self.assertEqual(content.media_types, ["video"])
        self.assertTrue(content.is_video_at(0))
        self.assertEqual(content.video_duration_sec, 54.0)
        self.assertEqual(content.extra["page_id"], "page-1")


@pytest.mark.django_db(transaction=True)
def test_published_story_gets_no_first_comment():
    from apps.composer.models import PlatformPost, Post
    from apps.organizations.models import Organization
    from apps.social_accounts.models import SocialAccount
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="Story Org")
    ws = Workspace.objects.create(name="Story WS", organization=org)
    posts = {}
    for name, extra in (("story", {"post_type": "story"}), ("feed", {})):
        account = SocialAccount.objects.create(
            workspace=ws,
            platform="instagram",
            account_platform_id=f"ig-{name}",
            account_name=name,
            connection_status="connected",
            oauth_access_token="tok",
        )
        post = Post.objects.create(workspace=ws, caption="E2E", first_comment="Link in der Bio")
        pp = PlatformPost.objects.create(post=post, social_account=account, status="scheduled", platform_extra=extra)
        posts[name] = (post, pp)

    def _fake_publish(pp):
        PlatformPost.objects.filter(id=pp.id).update(status=PlatformPost.Status.PUBLISHED, platform_post_id="m-1")
        return {"success": True}

    engine = PublishEngine()
    task = MagicMock()
    with (
        patch.object(engine, "_publish_platform_post", side_effect=_fake_publish),
        patch("apps.publisher.engine._post_first_comment_task", task),
    ):
        for post, pp in posts.values():
            engine._publish_post_group(post, [pp])

    commented = [call.args[0] for call in task.call_args_list]
    assert str(posts["feed"][1].id) in commented
    assert str(posts["story"][1].id) not in commented
