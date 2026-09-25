"""Access tokens stay usable for inbox, analytics and replies (apps/social_accounts/tokens.py).

Regression for 25.09.2026: the YouTube access token (one hour) expired at
08:38 UTC and the inbox sync failed with ``401 Invalid Credentials`` every
five minutes, because only publishing and the six-hourly health check
refreshed it.
"""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.social_accounts.models import SocialAccount
from apps.social_accounts.tokens import (
    ACCESS_TOKEN_REFRESH_MARGIN,
    call_with_fresh_token,
    fresh_access_token,
    is_auth_error,
    token_needs_refresh,
)
from providers.exceptions import APIError, OAuthError, TokenExpiredError
from providers.types import OAuthTokens

YOUTUBE_401 = APIError(
    'YouTube API error 401: {"error": {"code": 401, "message": "Invalid Credentials"}}',
    status_code=401,
    platform="YouTube",
)


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Token WS", organization=organization)


@pytest.fixture
def youtube_account(workspace):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="youtube",
        account_platform_id="UC-token-test",
        account_name="Token Test",
        oauth_access_token="old-access",
        oauth_refresh_token="refresh-1",
        token_expires_at=timezone.now() - timedelta(minutes=25),
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


def _provider(new_access="new-access", expires_in=3599):
    provider = MagicMock()
    provider.refresh_token.return_value = OAuthTokens(
        access_token=new_access,
        refresh_token=None,  # Google keeps the refresh token
        expires_in=expires_in,
    )
    return provider


# --- token_needs_refresh / is_auth_error ---------------------------------


@pytest.mark.django_db
def test_needs_refresh_when_expired_or_inside_margin(youtube_account):
    assert token_needs_refresh(youtube_account)
    youtube_account.token_expires_at = timezone.now() + ACCESS_TOKEN_REFRESH_MARGIN - timedelta(seconds=30)
    assert token_needs_refresh(youtube_account)


@pytest.mark.django_db
def test_no_refresh_for_valid_token_or_without_refresh_token(youtube_account):
    youtube_account.token_expires_at = timezone.now() + timedelta(minutes=50)
    assert not token_needs_refresh(youtube_account)
    youtube_account.token_expires_at = timezone.now() - timedelta(minutes=5)
    youtube_account.oauth_refresh_token = ""
    assert not token_needs_refresh(youtube_account)
    youtube_account.oauth_refresh_token = "refresh-1"
    youtube_account.token_expires_at = None
    assert not token_needs_refresh(youtube_account)


def test_auth_error_detection():
    assert is_auth_error(YOUTUBE_401)
    assert is_auth_error(TokenExpiredError("expired"))
    meta_expired = APIError(
        "Instagram API error 400",
        status_code=400,
        raw_response={"error": {"code": 190, "message": "Error validating access token"}},
    )
    assert is_auth_error(meta_expired)
    capability = APIError(
        "Instagram API error 400",
        status_code=400,
        raw_response={"error": {"code": 3, "message": "(#3) Application does not have the capability"}},
    )
    assert not is_auth_error(capability)
    assert not is_auth_error(APIError("boom", status_code=500))
    assert not is_auth_error(ValueError("x"))


# --- refresh before use --------------------------------------------------


@pytest.mark.django_db
def test_expired_token_is_refreshed_before_the_call_and_persisted(youtube_account):
    provider = _provider()
    seen = []

    result = call_with_fresh_token(youtube_account, provider, lambda token: seen.append(token) or "ok")

    assert result == "ok"
    assert seen == ["new-access"]
    provider.refresh_token.assert_called_once_with("refresh-1")
    youtube_account.refresh_from_db()
    assert youtube_account.oauth_access_token == "new-access"
    assert youtube_account.oauth_refresh_token == "refresh-1"
    assert youtube_account.token_expires_at > timezone.now() + timedelta(minutes=55)


@pytest.mark.django_db
def test_valid_token_is_used_without_refresh(youtube_account):
    youtube_account.token_expires_at = timezone.now() + timedelta(minutes=45)
    youtube_account.save()
    provider = _provider()

    assert call_with_fresh_token(youtube_account, provider, lambda token: token) == "old-access"
    provider.refresh_token.assert_not_called()


@pytest.mark.django_db
def test_failed_refresh_before_use_falls_back_to_stored_token(youtube_account):
    provider = MagicMock()
    provider.refresh_token.side_effect = OAuthError("network down")
    assert fresh_access_token(youtube_account, provider) == "old-access"


# --- refresh once on 401 -------------------------------------------------


@pytest.mark.django_db
def test_401_refreshes_once_and_retries(youtube_account):
    # token_expires_at says "valid", the platform says otherwise (revoked, skew).
    youtube_account.token_expires_at = timezone.now() + timedelta(minutes=45)
    youtube_account.save()
    provider = _provider(new_access="after-401")
    calls = []

    def call(token):
        calls.append(token)
        if token == "old-access":
            raise YOUTUBE_401
        return "messages"

    assert call_with_fresh_token(youtube_account, provider, call) == "messages"
    assert calls == ["old-access", "after-401"]
    provider.refresh_token.assert_called_once()


@pytest.mark.django_db
def test_second_401_propagates_without_a_loop(youtube_account):
    youtube_account.token_expires_at = timezone.now() + timedelta(minutes=45)
    youtube_account.save()
    provider = _provider()
    call = MagicMock(side_effect=YOUTUBE_401)

    with pytest.raises(APIError):
        call_with_fresh_token(youtube_account, provider, call)
    assert call.call_count == 2
    provider.refresh_token.assert_called_once()


@pytest.mark.django_db
def test_other_errors_are_not_retried(youtube_account):
    youtube_account.token_expires_at = timezone.now() + timedelta(minutes=45)
    youtube_account.save()
    provider = _provider()
    call = MagicMock(side_effect=APIError("quota", status_code=403))

    with pytest.raises(APIError):
        call_with_fresh_token(youtube_account, provider, call)
    assert call.call_count == 1
    provider.refresh_token.assert_not_called()


# --- concurrent refresh --------------------------------------------------


@pytest.mark.django_db
def test_refresh_adopts_token_refreshed_by_another_process(youtube_account):
    # Another process (web worker) refreshed in the meantime; this in-memory
    # copy still holds the old token. Rotating providers (Bluesky, TikTok)
    # would break if the stale refresh token were spent a second time.
    stale = SocialAccount.objects.get(pk=youtube_account.pk)
    fresh = SocialAccount.objects.get(pk=youtube_account.pk)
    fresh.oauth_access_token = "refreshed-elsewhere"
    fresh.oauth_refresh_token = "refresh-2"
    fresh.token_expires_at = timezone.now() + timedelta(minutes=59)
    fresh.save()

    provider = _provider()
    assert stale.refresh_oauth_token(provider) == "refreshed-elsewhere"
    provider.refresh_token.assert_not_called()
    assert stale.oauth_refresh_token == "refresh-2"


@pytest.mark.django_db
def test_refresh_uses_current_refresh_token_from_db(youtube_account):
    # The DB holds a rotated refresh token but its access token is expired
    # again: refresh with the DB's refresh token, not the stale in-memory one.
    stale = SocialAccount.objects.get(pk=youtube_account.pk)
    fresh = SocialAccount.objects.get(pk=youtube_account.pk)
    fresh.oauth_access_token = "also-expired"
    fresh.oauth_refresh_token = "refresh-2"
    fresh.token_expires_at = timezone.now() - timedelta(minutes=1)
    fresh.save()

    provider = _provider()
    assert stale.refresh_oauth_token(provider) == "new-access"
    provider.refresh_token.assert_called_once_with("refresh-2")
    stale.refresh_from_db()
    assert stale.oauth_refresh_token == "refresh-2"
