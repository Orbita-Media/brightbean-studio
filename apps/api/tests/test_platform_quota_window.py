"""Per-account platform cap counted by publish time (24-hour moving window).

Regression for 2026-09-24: the cap used to count every quota-consuming
row whose ``updated_at`` lay in the last 24 h. Planning six weeks of
one-per-day stories in one run therefore filled the Facebook bucket
(200/200) and locked the account for a day, although no single day had
more than a handful of posts. The cap mirrors the platform's own limit
("25 posts within a 24-hour moving period"), so rows now count at the
moment they are published.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from django.test import Client
from django.utils import timezone
from ninja.errors import HttpError

from apps.api.limits import (
    QUOTA_WINDOW,
    check_platform_quota,
    next_free_slot,
    window_load,
)
from apps.api_keys import services
from apps.composer.models import PlatformPost, Post
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="quota-owner@example.com",
        password="testpass123",
        name="Quota Owner",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def workspace(db, user):
    from apps.organizations.models import Organization
    from apps.workspaces.models import Workspace

    org = Organization.objects.create(name="Quota Org")
    ws = Workspace.objects.create(name="Quota WS", organization=org)
    OrgMembership.objects.create(user=user, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER)
    return ws


@pytest.fixture
def ig(db, workspace):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-quota",
        account_name="IG Quota",
        connection_status="connected",
    )


@pytest.fixture
def api_key(db, user, workspace, ig):
    key = services.issue_api_key(
        workspace=workspace,
        social_accounts=[ig],
        issued_by=user,
        name="quota",
        permissions=list(PERMISSION_KEYS),
    )
    # The 300-post plan below would otherwise trip the per-key HTTP
    # throttle (120 writes/min), which is a different tier.
    key.api_key.rate_override_writes = 100_000
    key.api_key.save(update_fields=["rate_override_writes"])
    return key


@pytest.fixture
def client(api_key):
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {api_key.plaintext_token}")


def _base() -> dt.datetime:
    """A fixed anchor two days ahead, on the full hour."""
    return (timezone.now() + dt.timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


def _row(account, *, status="scheduled", scheduled_at=None, published_at=None, post_scheduled_at=None):
    post = Post.objects.create(workspace=account.workspace, caption="pre", scheduled_at=post_scheduled_at)
    return PlatformPost.objects.create(
        post=post,
        social_account=account,
        status=status,
        scheduled_at=scheduled_at,
        published_at=published_at,
    )


def _schedule(client, account, when: dt.datetime, caption="x"):
    return client.post(
        "/api/v1/posts/",
        data=json.dumps(
            {
                "social_account_id": str(account.id),
                "caption": caption,
                "action": "schedule",
                "scheduled_at": when.isoformat(),
            }
        ),
        content_type="application/json",
    )


@pytest.mark.django_db
class TestPlanningAheadPasses:
    def test_300_posts_spread_over_60_days_all_go_through(self, client, ig):
        """Five Instagram posts a day for 60 days, planned in one run.

        Under the old ``updated_at`` count the 26th request 429'd.
        """
        base = _base()
        for day in range(60):
            for slot in range(5):
                when = base + dt.timedelta(days=day, hours=3 * slot)
                r = _schedule(client, ig, when, caption=f"d{day} s{slot}")
                assert r.status_code == 201, (day, slot, r.content)
        assert PlatformPost.objects.filter(social_account=ig, status="scheduled").count() == 300

    def test_full_day_does_not_block_another_day(self, ig):
        base = _base()
        for i in range(25):
            _row(ig, scheduled_at=base + dt.timedelta(minutes=10 * i))
        # A week later nothing is planned — must pass.
        check_platform_quota(ig, base + dt.timedelta(days=7))


@pytest.mark.django_db
class TestFullWindowBlocks:
    def test_26th_instagram_post_in_one_window_is_429(self, client, ig):
        base = _base()
        for i in range(25):
            r = _schedule(client, ig, base + dt.timedelta(minutes=30 * i), caption=f"p{i}")
            assert r.status_code == 201, r.content
        r = _schedule(client, ig, base + dt.timedelta(hours=5), caption="one too many")
        assert r.status_code == 429
        body = r.json()
        assert body["error"] == "rate_limited"
        assert body["tier"] == "platform_quota:instagram"
        assert body["limit"] == 25
        assert body["remaining"] == 0
        assert body["window_used"] == 25
        # The first post (``base``) leaves the window at base + 24h.
        assert dt.datetime.fromisoformat(body["next_free_at"]) == base + QUOTA_WINDOW
        assert body["retry_after"] == int((base + QUOTA_WINDOW - (base + dt.timedelta(hours=5))).total_seconds())
        assert int(r.headers["Retry-After"]) == body["retry_after"]
        assert "anderer Termin" in body["detail"]
        assert PlatformPost.objects.filter(social_account=ig).count() == 25

    def test_moving_window_not_calendar_day(self, ig):
        """25 posts in the evening + 1 the next morning break a 24-h window
        although they sit on two calendar days."""
        evening = _base().replace(hour=20)
        for i in range(25):
            _row(ig, scheduled_at=evening + dt.timedelta(minutes=5 * i))
        with pytest.raises(HttpError) as exc:
            check_platform_quota(ig, evening + dt.timedelta(hours=12))
        assert exc.value.status_code == 429
        # Exactly 24 h after the first post there is room again.
        check_platform_quota(ig, evening + QUOTA_WINDOW)
        assert next_free_slot(ig, evening + dt.timedelta(hours=12)) == evening + QUOTA_WINDOW

    def test_post_before_a_full_window_is_blocked_too(self, ig):
        """The new post may be the *first* of a window that later fills up."""
        base = _base()
        for i in range(25):
            _row(ig, scheduled_at=base + dt.timedelta(hours=10, minutes=i))
        with pytest.raises(HttpError):
            check_platform_quota(ig, base)
        check_platform_quota(ig, base - dt.timedelta(hours=14, minutes=1))

    def test_schedule_route_into_full_window_is_429(self, client, ig, workspace):
        base = _base()
        for i in range(25):
            _row(ig, scheduled_at=base + dt.timedelta(minutes=i))
        draft = _row(ig, status="draft")
        r = client.post(
            f"/api/v1/posts/{draft.post_id}/schedule",
            data=json.dumps({"scheduled_at": (base + dt.timedelta(hours=1)).isoformat()}),
            content_type="application/json",
        )
        assert r.status_code == 429, r.content
        draft.refresh_from_db()
        assert draft.status == "draft"
        # Same draft, a free day: goes through.
        r = client.post(
            f"/api/v1/posts/{draft.post_id}/schedule",
            data=json.dumps({"scheduled_at": (base + dt.timedelta(days=3)).isoformat()}),
            content_type="application/json",
        )
        assert r.status_code == 200, r.content


@pytest.mark.django_db
class TestDraftsNeverCount:
    def test_drafts_with_times_do_not_count(self, client, ig):
        base = _base()
        for _ in range(40):
            _row(ig, status="draft", scheduled_at=base, post_scheduled_at=base)
        assert window_load(ig, base) == 0
        r = _schedule(client, ig, base, caption="first real one")
        assert r.status_code == 201, r.content

    def test_other_editorial_states_do_not_count(self, ig):
        base = _base()
        for status in ("pending_review", "approved", "changes_requested", "rejected"):
            for _ in range(10):
                _row(ig, status=status, scheduled_at=base)
        assert window_load(ig, base) == 0


@pytest.mark.django_db
class TestReschedule:
    def test_patch_into_full_window_is_429_and_keeps_old_time(self, client, ig):
        base = _base()
        for i in range(25):
            _row(ig, scheduled_at=base + dt.timedelta(days=5, minutes=i))
        mover = _row(ig, scheduled_at=base, post_scheduled_at=base)
        r = client.patch(
            f"/api/v1/posts/{mover.post_id}",
            data=json.dumps({"scheduled_at": (base + dt.timedelta(days=5, hours=2)).isoformat()}),
            content_type="application/json",
        )
        assert r.status_code == 429, r.content
        assert r.json()["tier"] == "platform_quota:instagram"
        mover.refresh_from_db()
        assert mover.scheduled_at == base

    def test_patch_within_own_window_does_not_count_itself(self, client, ig):
        base = _base()
        for i in range(24):
            _row(ig, scheduled_at=base + dt.timedelta(minutes=i))
        mover = _row(ig, scheduled_at=base + dt.timedelta(hours=1), post_scheduled_at=base + dt.timedelta(hours=1))
        new_time = base + dt.timedelta(hours=2)
        r = client.patch(
            f"/api/v1/posts/{mover.post_id}",
            data=json.dumps({"scheduled_at": new_time.isoformat()}),
            content_type="application/json",
        )
        assert r.status_code == 200, r.content
        mover.refresh_from_db()
        assert mover.scheduled_at == new_time


@pytest.mark.django_db
class TestPublishTimeSources:
    def test_published_rows_count_at_published_at_not_updated_at(self, ig):
        now = timezone.now()
        # Published 30 h ago, touched just now (e.g. cover edit) — outside.
        for _ in range(25):
            _row(ig, status="published", published_at=now - dt.timedelta(hours=30))
        check_platform_quota(ig)  # publish now: fine

    def test_published_in_last_hours_block_publish_now_with_wait_time(self, ig):
        now = timezone.now()
        first = now - dt.timedelta(hours=2)
        for i in range(25):
            _row(ig, status="published", published_at=first + dt.timedelta(minutes=i))
        with pytest.raises(HttpError) as exc:
            check_platform_quota(ig)
        msg = exc.value.message
        retry = int(msg.split("retry_after=")[1].split()[0])
        # ~22 h until the first published post leaves the window.
        assert 21 * 3600 < retry <= 22 * 3600 + 5

    def test_failed_rows_count_at_updated_at(self, ig):
        for _ in range(25):
            _row(ig, status="failed")  # updated_at = now
        with pytest.raises(HttpError):
            check_platform_quota(ig)
        check_platform_quota(ig, timezone.now() + dt.timedelta(hours=25))

    def test_child_without_time_falls_back_to_post_time(self, ig):
        base = _base()
        for _ in range(25):
            _row(ig, scheduled_at=None, post_scheduled_at=base)
        with pytest.raises(HttpError):
            check_platform_quota(ig, base + dt.timedelta(hours=1))
        check_platform_quota(ig, base + dt.timedelta(days=2))

    def test_overdue_scheduled_rows_count_as_now(self, ig):
        """A scheduled row whose time already passed goes out on the next
        poll — it presses on the current window, not on its stale time."""
        stale = timezone.now() - dt.timedelta(days=3)
        for _ in range(25):
            _row(ig, scheduled_at=stale)
        with pytest.raises(HttpError):
            check_platform_quota(ig)

    def test_past_request_time_is_treated_as_now(self, ig):
        now = timezone.now()
        for i in range(25):
            _row(ig, scheduled_at=now + dt.timedelta(hours=1, minutes=i))
        with pytest.raises(HttpError):
            check_platform_quota(ig, now - dt.timedelta(days=10))

    def test_old_draft_scheduled_today_counts_at_new_time(self, ig):
        """Codex P3 stays closed: a draft created long ago and scheduled now
        is counted at its publish time, whatever its created_at."""
        base = _base()
        pp = _row(ig, status="draft")
        old = timezone.now() - dt.timedelta(days=10)
        PlatformPost.objects.filter(pk=pp.pk).update(created_at=old, updated_at=old)
        PlatformPost.objects.filter(pk=pp.pk).update(status="scheduled", scheduled_at=base)
        assert window_load(ig, base) == 1


@pytest.mark.django_db
class TestOverrides:
    def test_zero_override_locks_even_an_empty_day(self, ig):
        ig.daily_post_limit_override = 0
        ig.save(update_fields=["daily_post_limit_override"])
        with pytest.raises(HttpError):
            check_platform_quota(ig, _base() + dt.timedelta(days=30))
        assert next_free_slot(ig, _base()) is None


@pytest.mark.django_db
class TestMcpSchedulePost:
    def test_schedule_post_uses_the_requested_time(self, api_key, ig):
        from apps.mcp.tests.test_transport import _post, _rpc

        mcp = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {api_key.plaintext_token}")
        base = _base()
        for i in range(25):
            _row(ig, scheduled_at=base + dt.timedelta(minutes=i))

        def call(when):
            return _post(
                mcp,
                _rpc(
                    "tools/call",
                    {
                        "name": "schedule_post",
                        "arguments": {
                            "social_account_id": str(ig.id),
                            "caption": "via mcp",
                            "scheduled_at": when.isoformat(),
                        },
                    },
                ),
            )[1]

        full = call(base + dt.timedelta(hours=1))
        assert "error" in full, full
        assert "24-hour window full" in full["error"]["message"]
        ok = call(base + dt.timedelta(days=4))
        assert "error" not in ok, ok
