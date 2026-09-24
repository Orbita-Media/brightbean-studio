"""Pinterest board of a pin – per pin, with the account's default board as fallback.

Anlass (24.09.2026): ``providers/pinterest.py`` needs ``board_id`` in
``platform_extra``, but the Agent API could set neither ``board_id`` nor
``link_url``. Both scheduled pins had ``platform_extra == {}`` and would have
failed at their publish time, hours later and without anyone watching.

Now:

* ``platform_extra["board_id"]`` / ``["link_url"]`` per pin (API and composer),
* a default board per Pinterest account in ``SocialAccount.platform_settings``
  (account page, ``PUT /api/v1/accounts/{id}/pinterest-default-board``),
  used by the publish engine when a pin has no board of its own,
* a pin that has neither is refused BEFORE its date: scheduling answers 422.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

SETTING_BOARD_ID = "pinterest_default_board_id"
SETTING_BOARD_NAME = "pinterest_default_board_name"

#: Pinterest board ids are numeric strings ("1082834372847314271").
BOARD_ID_PATTERN = r"^[0-9]{1,32}$"
_BOARD_ID_RE = re.compile(BOARD_ID_PATTERN)

#: ``link`` on POST /v5/pins.
LINK_MAX_LENGTH = 2048


def is_board_id(value) -> bool:
    return bool(value) and bool(_BOARD_ID_RE.match(str(value)))


def default_board_id(account) -> str | None:
    """The account's default board, or None (also for non-Pinterest accounts)."""
    if getattr(account, "platform", "") != "pinterest":
        return None
    value = str((getattr(account, "platform_settings", None) or {}).get(SETTING_BOARD_ID) or "").strip()
    return value if is_board_id(value) else None


def default_board_name(account) -> str:
    return str((getattr(account, "platform_settings", None) or {}).get(SETTING_BOARD_NAME) or "")


def set_default_board(account, board_id: str | None, board_name: str = "") -> None:
    """Store (or with ``None`` remove) the default board; other settings stay."""
    settings = dict(account.platform_settings or {})
    if board_id:
        settings[SETTING_BOARD_ID] = str(board_id)
        settings[SETTING_BOARD_NAME] = str(board_name or "")[:200]
    else:
        settings.pop(SETTING_BOARD_ID, None)
        settings.pop(SETTING_BOARD_NAME, None)
    account.platform_settings = settings
    account.save(update_fields=["platform_settings", "updated_at"])


def effective_board_id(extra: dict | None, account) -> str | None:
    """The board a pin goes to: its own ``board_id``, else the account default."""
    own = str((extra or {}).get("board_id") or "").strip()
    return own or default_board_id(account)


def missing_board_message(account) -> str:
    name = getattr(account, "account_name", "") or "Pinterest"
    return (
        f"Pinterest ({name}): Kein Board gesetzt. Entweder platform_overrides[].board_id mitschicken "
        "(Liste: GET /api/v1/accounts/{id}/pinterest-boards) oder am Konto ein Standard-Board "
        "hinterlegen (PUT /api/v1/accounts/{id}/pinterest-default-board bzw. Kontenseite)."
    )


def is_valid_link(value: str) -> bool:
    """``https://host/...`` without spaces, at most 2048 characters."""
    from urllib.parse import urlparse

    if not value or len(value) > LINK_MAX_LENGTH or any(c.isspace() for c in value):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and "." in parsed.netloc


def fetch_boards(account) -> list[dict]:
    """All boards of the account as ``{"id", "name", "privacy"}``.

    Raises on a failed token refresh or API error – the caller decides how
    to report it.
    """
    from apps.credentials.models import resolve_platform_credentials
    from providers import get_provider

    provider = get_provider("pinterest", resolve_platform_credentials("pinterest", account.workspace.organization_id))
    access_token = account.oauth_access_token
    if account.token_expires_at and account.is_token_expiring_soon and account.oauth_refresh_token:
        access_token = account.refresh_oauth_token(provider)
    boards = provider.get_boards(access_token)
    return [
        {"id": str(b.get("id") or ""), "name": str(b.get("name") or ""), "privacy": str(b.get("privacy") or "")}
        for b in boards
        if b.get("id")
    ]
