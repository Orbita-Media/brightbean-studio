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
    PinterestBoard,
    PinterestBoardsResponse,
    PinterestDefaultBoardRequest,
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
    from apps.social_accounts.models import SocialAccount

    api_key = request.api_key
    # The key row (with its accounts) is cached for a short while; read the
    # accounts fresh so a just-changed setting (default board) shows at once.
    ids = [sa.id for sa in api_key.social_accounts.all()]
    fresh = {sa.id: sa for sa in SocialAccount.objects.filter(id__in=ids)}
    accounts = [AccountSummary.from_social_account(fresh[i]) for i in ids if i in fresh]
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


def _pinterest_account(request, account_id: uuid.UUID, action: str):
    """The Pinterest account of this key's allowlist (403 / 422 otherwise).

    Read fresh from the database: the key's account list is cached and may
    carry an old token or an old default board.
    """
    from apps.social_accounts.models import SocialAccount

    allowed = {sa.id for sa in request.api_key.social_accounts.all()}
    account = SocialAccount.objects.filter(id=account_id).first() if account_id in allowed else None
    if account is None:
        log_audit_entry(request, action=f"{action}.403", target_id=account_id, status_code=403)
        raise HttpError(403, "SocialAccount is not in this key's allowlist.")
    if account.platform != "pinterest":
        log_audit_entry(request, action=f"{action}.422", target_id=account_id, status_code=422)
        raise HttpError(422, f"Boards gibt es nur bei Pinterest; dieses Konto ist {account.platform}.")
    return account


@router.get(
    "/{account_id}/pinterest-boards",
    response=PinterestBoardsResponse,
    summary="Boards of a Pinterest account (id, name, privacy) for platform_overrides[].board_id",
)
def pinterest_boards(request, account_id: uuid.UUID):
    """All boards of the account, straight from Pinterest (``GET /v5/boards``).

    ``default_board_id`` is the account's default board – the one a pin
    without its own ``board_id`` goes to.
    """
    from apps.social_accounts.pinterest import default_board_id, fetch_boards

    enforce_http_rate_limits(request, is_write=False)
    account = _pinterest_account(request, account_id, "accounts.pinterest_boards")
    if not account.oauth_access_token:
        raise HttpError(409, "Pinterest-Konto ist nicht verbunden.")
    try:
        boards = fetch_boards(account)
    except Exception as exc:
        logger.warning("Pinterest boards lookup failed for %s: %s", account_id, exc)
        log_audit_entry(request, action="accounts.pinterest_boards.502", target_id=account_id, status_code=502)
        raise HttpError(502, "Pinterest hat die Boards nicht geliefert; später erneut versuchen.") from exc
    log_audit_entry(request, action="accounts.pinterest_boards.read", target_id=account_id, status_code=200)
    return PinterestBoardsResponse(
        boards=[PinterestBoard(**b) for b in boards],
        default_board_id=default_board_id(account),
    )


@router.put(
    "/{account_id}/pinterest-default-board",
    response=AccountSummary,
    summary="Set (or with null remove) the default board of a Pinterest account",
)
def set_pinterest_default_board(request, account_id: uuid.UUID, payload: PinterestDefaultBoardRequest):
    """The board a pin without its own ``board_id`` goes to.

    The id is checked against the account's real board list, so a typo does
    not surface only at publish time. Needs ``manage_social_accounts``.
    """
    from apps.social_accounts.pinterest import fetch_boards, set_default_board

    enforce_http_rate_limits(request, is_write=True)
    membership = getattr(request, "workspace_membership", None)
    if membership is None or not membership.effective_permissions.get("manage_social_accounts", False):
        raise HttpError(403, "Permission denied: manage_social_accounts")
    account = _pinterest_account(request, account_id, "accounts.pinterest_default_board")

    name = ""
    if payload.board_id is not None:
        try:
            boards = fetch_boards(account)
        except Exception as exc:
            logger.warning("Pinterest boards lookup failed for %s: %s", account_id, exc)
            raise HttpError(502, "Pinterest hat die Boards nicht geliefert; später erneut versuchen.") from exc
        match = next((b for b in boards if b["id"] == payload.board_id), None)
        if match is None:
            raise HttpError(
                422,
                f"Board {payload.board_id} gehört nicht zu diesem Pinterest-Konto; "
                "Liste: GET /api/v1/accounts/{id}/pinterest-boards.",
            )
        name = match["name"]
    set_default_board(account, payload.board_id, name)
    log_audit_entry(request, action="accounts.pinterest_default_board", target_id=account_id, status_code=200)
    return AccountSummary.from_social_account(account)
