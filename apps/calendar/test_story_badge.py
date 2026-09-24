"""Stories are recognisable in the calendar and the publish lists (badge „Story“).

A story planned 26 hours after its feed post looks like a second copy of that
post in every list – same image, same title. The badge tells them apart.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

BADGE = "data-story-badge"


class StoryBadgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="story@example.com", password="pw", tos_accepted_at=timezone.now())
        self.org = Organization.objects.create(name="Org")
        self.ws = Workspace.objects.create(organization=self.org, name="WS", timezone="Europe/Berlin")
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        WorkspaceMembership.objects.create(
            user=self.user, workspace=self.ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        self.ig = SocialAccount.objects.create(
            workspace=self.ws,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="Orbita",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)

    def _post(self, status, *, story, when=None):
        post = Post.objects.create(workspace=self.ws, author=self.user, caption="E2E-Story", scheduled_at=when)
        return PlatformPost.objects.create(
            post=post,
            social_account=self.ig,
            status=status,
            scheduled_at=when,
            platform_extra={"post_type": "story"} if story else {},
        )

    def test_model_properties(self):
        story = self._post("draft", story=True)
        feed = self._post("draft", story=False)
        self.assertTrue(story.is_story)
        self.assertFalse(feed.is_story)
        self.assertTrue(story.post.has_story)
        self.assertFalse(feed.post.has_story)

    def test_queue_shows_the_badge_only_on_the_story(self):
        when = timezone.now() + timedelta(days=2)
        self._post("scheduled", story=True, when=when)
        url = reverse("calendar:publish_tab_queue", kwargs={"workspace_id": self.ws.id})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, BADGE, count=1)

        self._post("scheduled", story=False, when=when)
        resp = self.client.get(url)
        self.assertContains(resp, BADGE, count=1)

    def test_drafts_tab_shows_the_badge(self):
        self._post("draft", story=True)
        resp = self.client.get(reverse("calendar:publish_tab_drafts", kwargs={"workspace_id": self.ws.id}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, BADGE, count=1)

    def test_month_calendar_shows_the_badge(self):
        when = timezone.now() + timedelta(hours=2)
        self._post("scheduled", story=True, when=when)
        url = reverse("calendar:calendar", kwargs={"workspace_id": self.ws.id})
        resp = self.client.get(url, {"view": "month", "date": when.date().isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, BADGE)

    def test_no_badge_without_a_story(self):
        when = timezone.now() + timedelta(hours=2)
        self._post("scheduled", story=False, when=when)
        url = reverse("calendar:calendar", kwargs={"workspace_id": self.ws.id})
        resp = self.client.get(url, {"view": "month", "date": when.date().isoformat()})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, BADGE)
