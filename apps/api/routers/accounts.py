"""``GET /api/v1/accounts`` – list the connected accounts this key may target."""

from __future__ import annotations

import logging
import uuid

from ninja import Router
from ninja.errors import HttpError

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.api.schemas import (
    AccountsListResponse,
    AccountSummary,
    AudioTrack,
    InstagramAudioResponse,
)

logger = logging.getLogger(__name__)

router = Router(tags=["accounts"])


@router.get(
    "/",
    response=AccountsListResponse,
    summary="List the SocialAccounts this API key is allowed to act on",
)
def list_accounts(request):
    enforce_http_rate_limits(request, is_write=False)
    api_key = request.api_key
    accounts = [AccountSummary.from_social_account(sa) for sa in api_key.social_accounts.all()]
    log_audit_entry(request, action="accounts.list", target_id=None, status_code=200)
    return AccountsListResponse(accounts=accounts)


@router.get(
    "/{account_id}/instagram-audio",
    response=InstagramAudioResponse,
    summary="Trending or searched Instagram sounds for a reel",
)
def instagram_audio(request, account_id: uuid.UUID, q: str = "", audio_type: str = "music"):
    """Look up sounds that ``POST /posts`` can attach via ``instagram_audio``.

    Without ``q`` this returns Meta's trending list, verbatim from the docs:
    "if no search query is provided, trending audio is returned". The
    catalogue only holds what Meta has cleared for third-party use, so it is
    smaller than the one in the app and a track can vanish from it. Treat an
    empty list as normal and publish without sound.
    """
    from apps.credentials.models import resolve_platform_credentials
    from providers import get_provider
    from providers.instagram import AUDIO_TYPES

    enforce_http_rate_limits(request, is_write=False)

    api_key = request.api_key
    account = next((sa for sa in api_key.social_accounts.all() if sa.id == account_id), None)
    if account is None:
        log_audit_entry(request, action="accounts.audio.403", target_id=account_id, status_code=403)
        raise HttpError(403, "SocialAccount is not in this key's allowlist.")
    if account.platform != "instagram":
        log_audit_entry(request, action="accounts.audio.422", target_id=account_id, status_code=422)
        raise HttpError(
            422,
            (f"Only Instagram can attach a platform sound through the API; this account is on {account.platform}."),
        )

    if audio_type not in AUDIO_TYPES:
        audio_type = "music"
    search_query = (q or "").strip()[:200]

    def _unavailable(reason: str) -> InstagramAudioResponse:
        log_audit_entry(request, action="accounts.audio.read", target_id=account_id, status_code=200)
        return InstagramAudioResponse(available=False, error=reason, tracks=[])

    access_token = account.oauth_access_token
    if not access_token:
        return _unavailable("Account is not connected")

    provider = get_provider(
        "instagram",
        resolve_platform_credentials("instagram", account.workspace.organization_id),
    )
    if account.token_expires_at and account.is_token_expiring_soon:
        try:
            access_token = account.refresh_oauth_token(provider)
        except Exception:
            return _unavailable("Token refresh failed")

    try:
        tracks = provider.list_audio(
            access_token,
            audio_type=audio_type,
            search_query=search_query,
            ig_user_id=account.account_platform_id,
        )
    except Exception as exc:
        logger.warning("Instagram audio lookup failed for %s: %s", account_id, exc)
        return _unavailable("Instagram did not return any audio")

    log_audit_entry(request, action="accounts.audio.read", target_id=account_id, status_code=200)
    return InstagramAudioResponse(
        available=True,
        trending=not search_query,
        tracks=[
            AudioTrack(
                id=t["id"],
                title=t["title"],
                artist=t["artist"],
                duration_ms=t["duration_ms"],
                cover_url=t["cover_url"],
            )
            for t in tracks
        ],
    )
