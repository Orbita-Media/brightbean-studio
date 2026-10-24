"""Shared helpers for finding a post that may already be live.

After a publish request whose answer got lost (``PublishOutcomeUnknownError``)
the engine asks the provider whether the post is on the account already
(``SocialProvider.find_published_post``). The providers list their newest
posts and look for the same text inside the attempt's time window; this
module holds the parts that do not depend on the platform.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from .exceptions import APIError, PublishLookupUnavailableError

# Pages of the newest posts a lookup reads at most. The window is the time
# since the first unclear attempt – minutes, rarely more than an hour – so a
# handful of pages is plenty; an account that posted more than that in the
# window cannot be checked reliably and goes to a human.
MAX_LOOKUP_PAGES = 5

# Meta Graph error codes that say "something went wrong, we do not know
# what": 1 = "An unknown error occurred", 2 = "An unexpected error has
# occurred. Please retry your request later." (the 500 of 22.10.2026 that
# had published anyway). Meta sends code 1 under HTTP 400 as well.
META_AMBIGUOUS_ERROR_CODES = {1, 2}

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Candidate:
    """One post as listed by the platform."""

    id: str
    text: str
    published_at: datetime | None
    url: str = ""


def is_meta_ambiguous_error(exc: APIError) -> bool:
    """Whether a Graph API error on the publish call leaves the outcome open."""
    if exc.status_code is None or exc.status_code >= 500:
        return True
    error = (exc.raw_response or {}).get("error")
    return isinstance(error, dict) and error.get("code") in META_AMBIGUOUS_ERROR_CODES


def normalize_text(text: str | None) -> str:
    """Compare texts the way a platform may have stored them.

    Platforms keep the text but may normalise line endings, trailing blanks
    or the Unicode form, so whitespace runs collapse to one space and the
    text is NFC-normalised before comparing.
    """
    if not text:
        return ""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def parse_time(value) -> datetime | None:
    """Parse the timestamps the platforms return (ISO 8601 with Z/+0000/+00:00, epoch)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Meta writes "+0000"; spelled "+00:00" so every fromisoformat accepts it.
    if re.search(r"[+-]\d{4}$", text):
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def find_by_text(
    fetch_page: Callable[[object], tuple[Iterable[Candidate], object]],
    *,
    text: str | None,
    since: datetime,
    exclude_ids: Iterable[str] = (),
    platform: str,
) -> Candidate | None:
    """Scan the newest posts for one with ``text`` published at or after ``since``.

    ``fetch_page(cursor)`` returns ``(candidates, next_cursor)``; the first
    call gets ``None``, a ``None`` cursor ends the list. Platforms list newest
    first, so the scan stops after the first page that reaches back past
    ``since``. Returns the earliest match – if the post is already there
    twice, the first one is the original.

    Raises ``PublishLookupUnavailableError`` when there is no text to compare (an
    image without caption could be any image) or the window holds more
    posts than ``MAX_LOOKUP_PAGES`` pages.
    """
    wanted = normalize_text(text)
    if not wanted:
        raise PublishLookupUnavailableError(
            f"Der Beitrag hat keinen Text, an dem er sich auf {platform} sicher wiedererkennen ließe.",
            platform=platform,
        )
    excluded = {str(i) for i in exclude_ids if i}
    matches: list[Candidate] = []
    cursor: object = None
    for _ in range(MAX_LOOKUP_PAGES):
        candidates, cursor = fetch_page(cursor)
        reached_since = False
        for candidate in candidates:
            if candidate.published_at is None:
                continue
            if candidate.published_at < since:
                reached_since = True
                continue
            if str(candidate.id) in excluded:
                continue
            if normalize_text(candidate.text) == wanted:
                matches.append(candidate)
        if reached_since or not cursor:
            break
    else:
        raise PublishLookupUnavailableError(
            f"Auf {platform} sind seit dem ersten Versuch zu viele Beiträge erschienen, um sicher nachzusehen.",
            platform=platform,
        )
    if not matches:
        return None
    return min(matches, key=lambda c: c.published_at)


def graph_page_fetcher(
    provider,
    access_token: str,
    url: str,
    *,
    fields: str,
    text_key: str,
    time_key: str,
    url_key: str,
    params: dict | None = None,
    id_of: Callable[[str], str] = str,
) -> Callable[[object], tuple[list[Candidate], object]]:
    """``fetch_page`` for a Meta Graph edge (cursor paging via ``paging.cursors.after``)."""

    def fetch(cursor):
        query = {"fields": fields, "limit": 50, **(params or {})}
        if cursor:
            query["after"] = cursor
        body = provider._request("GET", url, access_token=access_token, params=query).json()
        candidates = [
            Candidate(
                id=id_of(str(item.get("id", ""))),
                text=item.get(text_key) or "",
                published_at=parse_time(item.get(time_key)),
                url=item.get(url_key) or "",
            )
            for item in body.get("data") or []
            if item.get("id")
        ]
        paging = body.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
        return candidates, after

    return fetch


def lookup_instagram_media(
    provider,
    access_token: str,
    media_url: str,
    content,
    *,
    since: datetime,
    exclude_ids: Iterable[str] = (),
):
    """Find a feed post or reel by caption on an Instagram ``/media`` edge."""
    from .types import PublishResult

    found = find_by_text(
        graph_page_fetcher(
            provider,
            access_token,
            media_url,
            fields="id,caption,timestamp,permalink",
            text_key="caption",
            time_key="timestamp",
            url_key="permalink",
        ),
        text=content.text,
        since=since,
        exclude_ids=exclude_ids,
        platform=provider.platform_name,
    )
    if found is None:
        return None
    return PublishResult(
        platform_post_id=found.id,
        url=found.url or f"https://www.instagram.com/p/{found.id}/",
        extra={"adopted_existing_post": True},
    )
