"""Inbox sync keeps working with short-lived tokens and refused inbox parts.

Two failures seen every five minutes on 25.09.2026:
- YouTube: ``401 Invalid Credentials`` once the one-hour access token expired.
- Instagram: ``(#3) Application does not have the capability to make this
  API call`` from the Conversations (DM) endpoint.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.inbox.models import InboxMessage
from apps.inbox.tasks import (
    INBOX_UNAVAILABLE_KEY,
    INBOX_UNAVAILABLE_RECHECK,
    InboxSyncEngine,
    inbox_parts_to_skip,
)
from apps.social_accounts.models import SocialAccount
from providers.exceptions import APIError
from providers.instagram import InstagramProvider
from providers.types import InboxMessage as ProviderMessage
from providers.types import OAuthTokens

CAPABILITY_ERROR = APIError(
    'Instagram API error 400: {"error":{"message":"(#3) Application does not have the capability to make this API call."}}',
    status_code=400,
    platform="Instagram",
    raw_response={
        "error": {
            "message": "(#3) Application does not have the capability to make this API call.",
            "type": "OAuthException",
            "code": 3,
        }
    },
)


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Inbox Token WS", organization=organization)


@pytest.fixture
def youtube_account(workspace):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="youtube",
        account_platform_id="UC-inbox",
        account_name="YouTube Inbox",
        oauth_access_token="expired-access",
        oauth_refresh_token="refresh-yt",
        token_expires_at=timezone.now() - timedelta(minutes=30),
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


@pytest.fixture
def instagram_account(workspace):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="17841400000000001",
        account_name="Instagram Inbox",
        oauth_access_token="page-token",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


def _comment(message_id):
    return ProviderMessage(
        platform_message_id=message_id,
        sender_id="u1",
        sender_name="leserin",
        text="Tolles Buch",
        timestamp=timezone.now(),
        message_type="comment",
        extra={"comment_id": message_id},
    )


# --- YouTube: expired token --------------------------------------------------


@pytest.mark.django_db
def test_expired_youtube_token_is_refreshed_before_get_messages(youtube_account):
    with patch("apps.inbox.tasks.get_provider") as get_provider:
        provider = get_provider.return_value
        provider.refresh_token.return_value = OAuthTokens(access_token="fresh-access", expires_in=3599)
        provider.get_messages.return_value = [_comment("c1")]

        InboxSyncEngine().sync_all()

    provider.refresh_token.assert_called_once_with("refresh-yt")
    assert provider.get_messages.call_args.kwargs["access_token"] == "fresh-access"
    youtube_account.refresh_from_db()
    assert youtube_account.token_expires_at > timezone.now() + timedelta(minutes=55)
    assert InboxMessage.objects.filter(social_account=youtube_account).count() == 1


@pytest.mark.django_db
def test_youtube_401_refreshes_once_and_retries(youtube_account):
    # Stored expiry claims the token is valid, YouTube disagrees.
    youtube_account.token_expires_at = timezone.now() + timedelta(minutes=40)
    youtube_account.save()
    with patch("apps.inbox.tasks.get_provider") as get_provider:
        provider = get_provider.return_value
        provider.refresh_token.return_value = OAuthTokens(access_token="fresh-access", expires_in=3599)

        def get_messages(access_token, since):
            if access_token != "fresh-access":
                raise APIError("YouTube API error 401: Invalid Credentials", status_code=401)
            return [_comment("c2")]

        provider.get_messages.side_effect = get_messages
        InboxSyncEngine().sync_all()

    assert provider.get_messages.call_count == 2
    provider.refresh_token.assert_called_once()
    assert InboxMessage.objects.filter(social_account=youtube_account, platform_message_id="c2").exists()


# --- Instagram provider: comments + DM capability ------------------------------


def _media_response():
    response = MagicMock()
    response.json.return_value = {
        "data": [
            {
                "id": "m1",
                "username": "orbitamedia_verlag",
                "permalink": "https://www.instagram.com/p/abc/",
                "comments": {
                    "data": [
                        {
                            "id": "c-new",
                            "text": "Wo kann ich das kaufen?",
                            "username": "leserin",
                            "timestamp": "2026-09-24T15:27:30+0000",
                            "from": {"id": "42", "username": "leserin"},
                            "replies": {
                                "data": [
                                    {
                                        "id": "r-own",
                                        "text": "Im Shop!",
                                        "username": "orbitamedia_verlag",
                                        "timestamp": "2026-09-24T16:00:00+0000",
                                    },
                                    {
                                        "id": "r-other",
                                        "text": "Danke",
                                        "username": "leser2",
                                        "timestamp": "2026-09-24T16:30:00+0000",
                                    },
                                ]
                            },
                        },
                        {
                            "id": "c-old",
                            "text": "alt",
                            "username": "altleser",
                            "timestamp": "2026-09-01T10:00:00+0000",
                        },
                    ]
                },
            }
        ]
    }
    return response


def _instagram_provider(dm_error=CAPABILITY_ERROR):
    provider = InstagramProvider({"ig_user_id": "17841400000000001"})

    def fake_request(method, url, **kwargs):
        if url.endswith("/media"):
            return _media_response()
        if url.endswith("/conversations"):
            raise dm_error
        raise AssertionError(f"unexpected call {method} {url}")

    provider._request = MagicMock(side_effect=fake_request)
    return provider


def test_instagram_returns_comments_although_dms_are_refused():
    from datetime import UTC, datetime

    provider = _instagram_provider()
    since = datetime(2026, 9, 20, tzinfo=UTC)

    messages = provider.get_messages("page-token", since=since)

    ids = [m.platform_message_id for m in messages]
    assert ids == ["c-new", "r-other"]  # own reply and pre-`since` comment left out
    assert all(m.message_type == "comment" for m in messages)
    assert messages[1].extra["parent_comment_id"] == "c-new"
    assert "capability" in provider.inbox_unavailable_parts["dm"]


def test_instagram_skips_parts_it_is_told_to_skip():
    provider = _instagram_provider()
    provider.inbox_skip_parts = {"dm"}

    provider.get_messages("page-token")

    urls = [c.args[1] for c in provider._request.call_args_list]
    assert not any(u.endswith("/conversations") for u in urls)
    assert provider.inbox_unavailable_parts == {}


def test_instagram_other_dm_errors_still_raise():
    provider = _instagram_provider(dm_error=APIError("server", status_code=500, raw_response={}))
    with pytest.raises(APIError):
        provider.get_messages("page-token")


def test_instagram_reply_to_comment_goes_to_replies_endpoint():
    provider = InstagramProvider({"ig_user_id": "17841400000000001"})
    response = MagicMock()
    response.json.return_value = {"id": "reply-1"}
    provider._request = MagicMock(return_value=response)

    provider.reply_to_message("tok", "r-other", "Danke!", extra={"comment_id": "r-other", "parent_comment_id": "c-new"})

    method, url = provider._request.call_args.args
    assert method == "POST" and url.endswith("/c-new/replies")
    assert provider._request.call_args.kwargs["params"] == {"message": "Danke!"}


def test_instagram_reply_to_dm_uses_conversation():
    provider = InstagramProvider({"ig_user_id": "17841400000000001"})
    response = MagicMock()
    response.json.return_value = {"id": "m-1"}
    provider._request = MagicMock(return_value=response)

    provider.reply_to_message("tok", "msg-9", "Hallo", extra={"conversation_id": "conv-1"})

    assert provider._request.call_args.args[1].endswith("/conv-1/messages")


# --- Engine: refused part is recorded, skipped, re-probed ----------------------


@pytest.mark.django_db
def test_engine_records_refused_dm_part_and_skips_it(instagram_account, caplog):
    with patch("apps.inbox.tasks.get_provider", return_value=_instagram_provider()):
        InboxSyncEngine().sync_all()

    instagram_account.refresh_from_db()
    marked = instagram_account.platform_settings[INBOX_UNAVAILABLE_KEY]
    assert set(marked) == {"dm"}
    assert "capability" in marked["dm"]["reason"]
    assert InboxMessage.objects.filter(social_account=instagram_account).count() == 3
    assert "get_messages() failed" not in caplog.text
    assert inbox_parts_to_skip(instagram_account) == {"dm"}

    # Next cycle: the DM endpoint is not called at all.
    second = _instagram_provider()
    with patch("apps.inbox.tasks.get_provider", return_value=second):
        InboxSyncEngine().sync_all()
    urls = [c.args[1] for c in second._request.call_args_list]
    assert not any(u.endswith("/conversations") for u in urls)
    instagram_account.refresh_from_db()
    assert set(instagram_account.platform_settings[INBOX_UNAVAILABLE_KEY]) == {"dm"}


@pytest.mark.django_db
def test_engine_reprobes_after_a_day_and_clears_recovered_part(instagram_account):
    old = (timezone.now() - INBOX_UNAVAILABLE_RECHECK - timedelta(minutes=1)).isoformat()
    instagram_account.platform_settings = {INBOX_UNAVAILABLE_KEY: {"dm": {"reason": "(#3)", "checked_at": old}}}
    instagram_account.save()
    assert inbox_parts_to_skip(instagram_account) == set()

    ok_dm = MagicMock()
    ok_dm.json.return_value = {"data": []}
    provider = InstagramProvider({"ig_user_id": "17841400000000001"})
    provider._request = MagicMock(
        side_effect=lambda method, url, **kw: _media_response() if url.endswith("/media") else ok_dm
    )
    with patch("apps.inbox.tasks.get_provider", return_value=provider):
        InboxSyncEngine().sync_all()

    instagram_account.refresh_from_db()
    assert INBOX_UNAVAILABLE_KEY not in instagram_account.platform_settings
