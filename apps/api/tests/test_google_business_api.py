"""Agent API: button and link of a Google Business local post.

Anlass (25.09.2026): Google released the Business Profile API for our project.
The provider could not set the "Kaufen" button because the API allowed
``link_url`` only for Pinterest (422 for every other channel) and had no field
for the button type.

Pinned here: ``link_url`` and ``gbp_cta`` on POST and PATCH for
``google_business``, read back in ``platform_overrides``; ``gbp_cta`` only on
Google Business; a button other than CALL cannot be scheduled without its link
(on create, on ``/schedule`` and when PATCH clears the link of a scheduled
post). Pinterest keeps its own rules (``test_pinterest_api.py``).
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.api.tests.test_pinterest_api import _account, _asset, _client, _out, _patch, _pin_post, _post
from apps.composer.models import PlatformPost, Post
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership

PARENT = "accounts/113894727651234567890/locations/2832287425028066333"
LINK = "https://orbita-media.de/buecher/festland?utm_source=google&utm_medium=social&utm_campaign=gbp-2026-10-15"


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="gbp@example.com", password="testpass123", name="GBP", tos_accepted_at=timezone.now()
    )


@pytest.fixture
def workspace(db):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="GBP Org")
    return Workspace.objects.create(name="GBP WS", organization=org)


@pytest.fixture
def memberships(db, user, workspace):
    OrgMembership.objects.create(user=user, organization=workspace.organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )


@pytest.fixture
def accounts(db, workspace):
    return {
        "google_business": _account(workspace, "google_business", PARENT),
        "instagram": _account(workspace, "instagram", "17841400000000000"),
    }


@pytest.fixture
def client(user, memberships, workspace, accounts):
    return _client(user, workspace, accounts, list(PERMISSION_KEYS))


@pytest.fixture
def jpeg(workspace):
    return _asset(workspace, "festland.jpg", "image")


def _body(account, *, media=(), action="draft", **override):
    body = {
        "social_account_id": str(account.id),
        "caption": "E2E-GBP Sieben Tage, vier Menschen, eine Insel.",
        "media_asset_ids": [str(m.id) for m in media],
        "action": action,
    }
    if override:
        body["platform_overrides"] = [{"social_account_id": str(account.id), **override}]
    if action == "schedule":
        body["scheduled_at"] = (timezone.now() + timedelta(days=4)).isoformat()
    return body


@pytest.mark.django_db
class TestCreate:
    def test_shop_button_and_link_are_stored_and_read_back(self, client, accounts, jpeg):
        gbp = accounts["google_business"]
        r = _post(client, _body(gbp, media=[jpeg], action="schedule", link_url=LINK, gbp_cta="SHOP"))
        assert r.status_code == 201, r.content
        pp = PlatformPost.objects.get(post_id=r.json()["id"])
        assert pp.platform_extra == {"link_url": LINK, "gbp_cta": "SHOP"}
        assert pp.status == "scheduled"
        out = _out(client.get(f"/api/v1/posts/{r.json()['id']}"), gbp)
        assert out["link_url"] == LINK
        assert out["gbp_cta"] == "SHOP"
        assert out["board_id"] is None

    def test_button_type_is_google_business_only(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["instagram"], media=[jpeg], gbp_cta="SHOP"))
        assert r.status_code == 422
        assert "nur bei Google Business" in r.json()["detail"]

    def test_board_is_still_pinterest_only(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["google_business"], media=[jpeg], board_id="1082834372847314271"))
        assert r.status_code == 422
        assert "nur bei Pinterest" in r.json()["detail"]

    def test_unknown_button_type_is_refused(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["google_business"], media=[jpeg], link_url=LINK, gbp_cta="BUY"))
        assert r.status_code == 422

    def test_link_must_be_https(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["google_business"], media=[jpeg], link_url="http://orbita-media.de"))
        assert r.status_code == 422

    def test_scheduling_a_button_without_link_is_refused_before_the_date(self, client, accounts, jpeg):
        r = _post(client, _body(accounts["google_business"], media=[jpeg], action="schedule", gbp_cta="SHOP"))
        assert r.status_code == 422, r.content
        assert "braucht link_url" in r.json()["detail"]
        assert not Post.objects.filter(caption__startswith="E2E-GBP").exists()

    def test_a_draft_may_stay_without_link_and_a_post_without_button_is_fine(self, client, accounts, jpeg):
        gbp = accounts["google_business"]
        assert _post(client, _body(gbp, media=[jpeg], gbp_cta="SHOP")).status_code == 201
        assert _post(client, _body(gbp, media=[jpeg], action="schedule")).status_code == 201
        r = _post(client, _body(gbp, media=[jpeg], action="schedule", gbp_cta="CALL"))
        assert r.status_code == 201, r.content


@pytest.mark.django_db
class TestPatchAndSchedule:
    def test_scheduled_post_gets_button_and_link(self, client, workspace, accounts, jpeg):
        gbp = accounts["google_business"]
        post = _pin_post(workspace, gbp, [jpeg])
        body = {"platform_overrides": [{"social_account_id": str(gbp.id), "gbp_cta": "SHOP", "link_url": LINK}]}
        r = _patch(client, post.id, body)
        assert r.status_code == 200, r.content
        pp = post.platform_posts.get()
        assert pp.status == "scheduled"
        assert pp.platform_extra == {"gbp_cta": "SHOP", "link_url": LINK}

    def test_clearing_the_link_of_a_scheduled_shop_post_is_refused(self, client, workspace, accounts, jpeg):
        gbp = accounts["google_business"]
        post = _pin_post(workspace, gbp, [jpeg], extra={"gbp_cta": "SHOP", "link_url": LINK})
        r = _patch(client, post.id, {"platform_overrides": [{"social_account_id": str(gbp.id), "link_url": None}]})
        assert r.status_code == 422
        assert post.platform_posts.get().platform_extra["link_url"] == LINK
        # Removing the button together with the link is fine.
        r = _patch(
            client,
            post.id,
            {"platform_overrides": [{"social_account_id": str(gbp.id), "link_url": None, "gbp_cta": None}]},
        )
        assert r.status_code == 200, r.content
        assert post.platform_posts.get().platform_extra == {}

    def test_schedule_route_refuses_a_button_without_link(self, client, workspace, accounts, jpeg):
        gbp = accounts["google_business"]
        post = _pin_post(workspace, gbp, [jpeg], status="draft", extra={"gbp_cta": "SHOP"})
        when = (timezone.now() + timedelta(days=5)).isoformat()
        r = client.post(
            f"/api/v1/posts/{post.id}/schedule",
            data=json.dumps({"scheduled_at": when}),
            content_type="application/json",
        )
        assert r.status_code == 422
        assert post.platform_posts.get().status == "draft"
