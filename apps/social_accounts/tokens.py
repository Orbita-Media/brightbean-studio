"""Keep OAuth access tokens usable for every background caller.

Why this exists (25.09.2026): YouTube and Google Business hand out access
tokens that live for one hour. Only the publish engine and a few composer
views refreshed them before use; the six-hourly health check refreshed them
as a side effect. Inbox sync (every five minutes) and the analytics sync
(hourly) used ``account.oauth_access_token`` as stored, so for roughly five
of every six hours they ran with an expired token and failed with
``401 Invalid Credentials``.

Every caller that talks to a provider on behalf of an account goes through
``call_with_fresh_token``: it refreshes shortly *before* expiry and, should
the platform still answer "unauthenticated" (clock skew, revoked token,
missing ``token_expires_at``), refreshes exactly once and repeats the call.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from django.utils import timezone

from providers.exceptions import APIError, TokenExpiredError

logger = logging.getLogger(__name__)

# Refresh this long before the stored expiry. Five-minute inbox cycles plus
# a slow provider call must never straddle the expiry instant.
ACCESS_TOKEN_REFRESH_MARGIN = timedelta(minutes=10)

# Meta signals an expired/invalid session token with HTTP 400 and error
# code 190 instead of a 401.
_META_INVALID_TOKEN_CODE = 190


def token_needs_refresh(account, margin: timedelta = ACCESS_TOKEN_REFRESH_MARGIN) -> bool:
    """True when the stored access token is expired or expires within *margin*."""
    if not account.oauth_refresh_token or not account.token_expires_at:
        return False
    return account.token_expires_at <= timezone.now() + margin


def is_auth_error(exc: Exception) -> bool:
    """True when *exc* means "the access token is not (or no longer) valid"."""
    if isinstance(exc, TokenExpiredError):
        return True
    if isinstance(exc, APIError):
        if exc.status_code == 401:
            return True
        error = (exc.raw_response or {}).get("error")
        if isinstance(error, dict) and error.get("code") == _META_INVALID_TOKEN_CODE:
            return True
    return False


def fresh_access_token(account, provider, margin: timedelta = ACCESS_TOKEN_REFRESH_MARGIN) -> str:
    """Return a usable access token, refreshing it first when it is about to expire.

    Best effort: if the refresh fails the stored token is returned and the
    provider call surfaces the real error.
    """
    if token_needs_refresh(account, margin):
        try:
            token = account.refresh_oauth_token(provider)
            logger.info(
                "Refreshed access token for account %s (%s) before use, valid until %s",
                account.id,
                account.platform,
                account.token_expires_at,
            )
            return token
        except Exception as exc:
            logger.warning(
                "Token refresh before use failed for account %s (%s): %s",
                account.id,
                account.platform,
                exc,
            )
    return account.oauth_access_token


def call_with_fresh_token[T](account, provider, call: Callable[[str], T]) -> T:
    """Run ``call(access_token)`` with a fresh token; on an auth error refresh once and retry.

    Only meant for idempotent reads (inbox, analytics) and for single-request
    writes that the platform rejected before doing anything (a 401 means the
    request was not executed).
    """
    token = fresh_access_token(account, provider)
    try:
        return call(token)
    except Exception as exc:
        if not is_auth_error(exc) or not account.oauth_refresh_token:
            raise
        logger.info(
            "Account %s (%s) got an auth error, refreshing token once and retrying",
            account.id,
            account.platform,
        )
        # A failing refresh propagates: the account then really needs a reconnect.
        token = account.refresh_oauth_token(provider)
        return call(token)
