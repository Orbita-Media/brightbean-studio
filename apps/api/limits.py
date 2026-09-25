"""Rate-limit primitives for the Agent API.

Two orthogonal concerns live here:

1. ``PLATFORM_DAILY_POST_LIMIT`` — channel-aligned caps on how many
   PlatformPost rows may be *published* per ``SocialAccount`` within any
   24-hour moving window. Rows are placed on the timeline at their
   publish time (``scheduled_at`` for queued rows, ``published_at`` for
   finished ones), not at the moment the agent created or scheduled them,
   so planning weeks ahead never trips the cap. Numbers come from each
   platform's own developer docs (May 2026); see ``README.md`` (Agent API,
   Rate Limits) and ``docs/PLATTFORM-GRENZEN.md``. The publisher's own
   ``RateLimitState`` tracks the *outgoing* upstream platform quota
   separately — these layers compose; neither replaces the other.

2. Per-key / per-workspace / per-IP HTTP throttles via ``django-ratelimit``,
   exposed as small wrapper helpers so each router stays declarative.

The 429 body shape is uniform across all tiers so agents can self-throttle
on ``tier`` without parsing free text.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.cache import cache as _cache
from django.http import HttpRequest
from django.utils import timezone
from django_ratelimit.core import is_ratelimited
from ninja.errors import HttpError

from apps.api_keys.models import ApiKey
from apps.composer.models import PlatformPost
from apps.social_accounts.models import SocialAccount

# ---------------------------------------------------------------------------
# Tier 2 — channel-aligned per-(SocialAccount, 24h) creation caps
# ---------------------------------------------------------------------------

#: Default platform cap when ``daily_post_limit_override`` on the
#: ``SocialAccount`` row isn't set. Numbers chosen at or just below the
#: platform's own published cap; see the plan file for justification and
#: source links. Lower bound (``_DEFAULT_FALLBACK``) covers unknown future
#: platforms safely.
#:
#: Keys MUST match the actual ``SocialAccount.platform`` values, which
#: come from ``apps.credentials.models.PlatformCredential.Platform`` —
#: not invented variants. Codex review flagged the previous
#: ``"facebook_page"`` key as a no-op (the real platform code is
#: ``"facebook"``), which silently dropped every Facebook account into
#: ``_DEFAULT_FALLBACK=50/day`` instead of the intended 200/day cap.
PLATFORM_DAILY_POST_LIMIT: dict[str, int] = {
    "linkedin_personal": 100,
    "linkedin_company": 100,
    "facebook": 200,
    "instagram": 25,
    "instagram_login": 25,
    "tiktok": 15,
    "youtube": 50,
    "pinterest": 100,
    "threads": 250,
    "mastodon": 200,
    "bluesky": 200,
    "google_business": 50,
}

_DEFAULT_FALLBACK = 50


def resolve_platform_limit(social_account: SocialAccount) -> int:
    """Effective per-24h cap for one ``SocialAccount``.

    Order: per-account override → platform default → fallback.

    Note the explicit ``is not None`` check: ``daily_post_limit_override``
    is a ``PositiveIntegerField(null=True)``, so an admin can legitimately
    set it to ``0`` to lock the account out of any creation. A naked
    ``if override:`` would treat 0 as "no override" and fall through to
    the platform default (e.g. LinkedIn 100/day), silently defeating the
    lockout.
    """
    override = getattr(social_account, "daily_post_limit_override", None)
    if override is not None:
        return int(override)
    return PLATFORM_DAILY_POST_LIMIT.get(social_account.platform, _DEFAULT_FALLBACK)


#: Statuses that *consume* the upstream platform's posting budget.
#:
#: A row only pressures the platform from the moment it leaves "draft" —
#: scheduled rows are queued for the publisher, publishing rows are
#: mid-flight on a platform API call, published rows already spent a
#: slot, and failed rows almost certainly did too (the platform 4xx'd
#: AFTER we made the call). Drafts are local DB state only; they have
#: not touched the platform yet, so counting them would block scheduling
#: for an agent who built a 25-post Instagram draft queue.
QUOTA_CONSUMING_STATUSES = frozenset({"scheduled", "publishing", "published", "failed"})

#: Length of the platform's moving window. Every platform in
#: ``PLATFORM_DAILY_POST_LIMIT`` states its cap per 24 hours.
QUOTA_WINDOW = dt.timedelta(hours=24)

#: How far past the requested time ``next_free_slot`` looks for a gap.
#: A week covers every realistic re-plan; beyond that the 429 simply
#: omits ``next_free_at``.
_NEXT_FREE_HORIZON = dt.timedelta(days=7)

_PENDING_STATUSES = ("scheduled", "publishing")
_DONE_STATUSES = ("published", "failed")


def _aware(value: dt.datetime) -> dt.datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, dt.UTC)
    return value


def effective_publish_at(publish_at: dt.datetime | None, *, now: dt.datetime | None = None) -> dt.datetime:
    """The moment a post will actually hit the platform.

    ``None`` means "publish now"; a time in the past is picked up by the
    publisher on its next poll, so it also lands at ``now``.
    """
    now = now or timezone.now()
    if publish_at is None:
        return now
    return max(_aware(publish_at), now)


def publish_times(
    social_account: SocialAccount,
    *,
    start: dt.datetime,
    end: dt.datetime,
    exclude_ids: tuple | list | set = (),
    now: dt.datetime | None = None,
) -> list[dt.datetime]:
    """Sorted publish times of every quota-consuming row in ``(start, end)``.

    The quota mirrors the platform's own cap, and the platform counts a
    post when it is *published* — not when we queued it. So each row is
    placed on the timeline at its publish moment:

    * ``scheduled`` / ``publishing``: the publisher's own time,
      ``Coalesce(PlatformPost.scheduled_at, Post.scheduled_at)``. An
      overdue row (time already past, publisher not yet through) goes
      out on the next poll, so it is clamped to ``now``. Rows without
      any time fall back to ``updated_at`` (clamped as well).
    * ``published`` / ``failed``: ``published_at``, else ``updated_at``
      (the moment the failed attempt was recorded).
    * drafts and every other editorial state: never counted.

    The previous implementation counted rows whose ``updated_at`` lay in
    the last 24 h, regardless of when they would publish. Planning six
    weeks of one-per-day stories therefore burned the whole daily cap in
    one run and locked the account for 24 h (Facebook 200/200 on
    2026-09-24). Counting by publish time keeps the Codex P3 guarantee
    too: a draft created long ago and scheduled today is counted at its
    new publish time, so "drafts first, schedule later" cannot bypass
    the cap.
    """
    now = now or timezone.now()
    from django.db.models import Case, DateTimeField, F, Value, When
    from django.db.models.functions import Coalesce, Greatest

    pending_at = Greatest(
        Coalesce(F("scheduled_at"), F("post__scheduled_at"), F("updated_at")),
        Value(now, output_field=DateTimeField()),
    )
    done_at = Coalesce(F("published_at"), F("updated_at"))
    qs = (
        PlatformPost.objects.filter(
            social_account=social_account,
            status__in=QUOTA_CONSUMING_STATUSES,
        )
        .annotate(
            quota_at=Case(
                When(status__in=_PENDING_STATUSES, then=pending_at),
                default=done_at,
                output_field=DateTimeField(),
            )
        )
        .filter(quota_at__gt=start, quota_at__lt=end)
    )
    if exclude_ids:
        qs = qs.exclude(pk__in=list(exclude_ids))
    return sorted(qs.values_list("quota_at", flat=True))


def _busiest_window(times: list[dt.datetime], at: dt.datetime, limit: int) -> int:
    """Largest number of existing posts sharing one 24-h window with ``at``.

    Exact moving-window check, as the platforms define it ("25 posts
    within a 24-hour moving period"): adding a post at ``at`` breaks the
    cap iff ``limit`` existing posts and ``at`` fit into one window,
    i.e. some run of ``limit + 1`` consecutive sorted times that
    contains ``at`` spans less than 24 h. Consecutive runs are the
    tightest ones, so checking them is sufficient.

    Returns the count of existing posts in the fullest window around
    ``at`` (capped at what matters for the verdict). ``>= limit`` means
    "no room".
    """
    import bisect

    merged = list(times)
    k = bisect.bisect_right(merged, at)
    merged.insert(k, at)
    best = 0
    # Windows containing ``at``: [merged[i], merged[i] + 24h) for i <= k
    # with merged[i] > at - 24h. Count members of each.
    for i in range(k, -1, -1):
        if at - merged[i] >= QUOTA_WINDOW:
            break
        j = bisect.bisect_left(merged, merged[i] + QUOTA_WINDOW)
        best = max(best, j - i - 1)  # minus the new post itself
        if best >= limit:
            break
    return best


def window_load(
    social_account: SocialAccount,
    publish_at: dt.datetime | None = None,
    *,
    exclude_ids: tuple | list | set = (),
) -> int:
    """How many quota-consuming posts share the fullest 24-h window with ``publish_at``."""
    now = timezone.now()
    at = effective_publish_at(publish_at, now=now)
    limit = resolve_platform_limit(social_account)
    times = publish_times(
        social_account,
        start=at - QUOTA_WINDOW,
        end=at + QUOTA_WINDOW,
        exclude_ids=exclude_ids,
        now=now,
    )
    return _busiest_window(times, at, max(limit, 1))


def next_free_slot(
    social_account: SocialAccount,
    publish_at: dt.datetime | None = None,
    *,
    exclude_ids: tuple | list | set = (),
) -> dt.datetime | None:
    """Earliest time ``>= publish_at`` where one more post fits under the cap.

    A window frees up only when an existing post drops out of it, so the
    candidates are the requested time itself and every ``t + 24h``.
    Returns ``None`` if the next week has no gap (or the cap is 0).
    """
    now = timezone.now()
    at = effective_publish_at(publish_at, now=now)
    limit = resolve_platform_limit(social_account)
    if limit <= 0:
        return None
    horizon = at + _NEXT_FREE_HORIZON
    times = publish_times(
        social_account,
        start=at - QUOTA_WINDOW,
        end=horizon + QUOTA_WINDOW,
        exclude_ids=exclude_ids,
        now=now,
    )
    candidates = sorted({at, *(t + QUOTA_WINDOW for t in times if at < t + QUOTA_WINDOW <= horizon)})
    for cand in candidates:
        if _busiest_window(times, cand, limit) < limit:
            return cand
    return None


def check_platform_quota(
    social_account: SocialAccount,
    publish_at: dt.datetime | None = None,
    *,
    exclude_ids: tuple | list | set = (),
) -> None:
    """Raise ``HttpError(429, ...)`` if a post at ``publish_at`` breaks the cap.

    Call this before a ``PlatformPost`` enters a quota-consuming state
    (create with ``action="schedule"``, ``/schedule``, PATCH with a new
    ``scheduled_at`` on a scheduled post, MCP ``schedule_post`` /
    ``schedule_draft``). ``publish_at`` is the time the post will be
    published; ``None`` or a past time means "now".

    ``exclude_ids`` names rows that are being *moved* (re-timing a
    scheduled post) so they don't block their own new slot.

    The 429 carries ``retry_after`` = seconds the post has to move later
    to fit (for a post due now that equals the wait time), plus
    ``next_free_at`` (ISO-8601) when a gap exists within a week.
    Waiting without changing the time does not help a future post: its
    window is full at that time no matter when the request is repeated.
    """
    now = timezone.now()
    at = effective_publish_at(publish_at, now=now)
    limit = resolve_platform_limit(social_account)
    used = window_load(social_account, at, exclude_ids=exclude_ids)
    if used < limit:
        return
    free = next_free_slot(social_account, at, exclude_ids=exclude_ids)
    if free is not None:
        retry_after_seconds = max(int((free - at).total_seconds()), 1)
    else:
        retry_after_seconds = int(_NEXT_FREE_HORIZON.total_seconds())
    extra = {"window_used": used, "requested_at": at.isoformat()}
    if free is not None:
        extra["next_free_at"] = free.isoformat()
    detail = (
        f"Das 24-Stunden-Fenster um {at.isoformat()} ist für dieses Konto voll "
        f"({used} von {limit} Beiträgen nach Veröffentlichungszeit). "
        + (
            f"Frei ist der nächste Termin ab {free.isoformat()}; "
            if free is not None
            else "In den folgenden sieben Tagen ist kein Termin frei; "
        )
        + "ein anderer Termin hilft, erneutes Senden mit demselben Termin nicht."
    )
    raise HttpError(
        429,
        _format_quota_message(
            tier=f"platform_quota:{social_account.platform}",
            limit=limit,
            remaining=0,
            retry_after=retry_after_seconds,
            extra=extra,
            detail=detail,
        ),
    )


def _format_quota_message(
    *,
    tier: str,
    limit: int,
    remaining: int,
    retry_after: int,
    extra: dict | None = None,
    detail: str = "",
) -> str:
    """Plain-text body for the HttpError so Ninja's default 429 page renders.

    The router-level error handler in ``api.py`` rewraps this into the
    uniform JSON shape with a ``Retry-After`` header. ``extra`` values
    must not contain whitespace (they become ``key=value`` tokens);
    ``detail`` is free text after a `` | `` separator and lands in the
    JSON body as ``detail``.
    """
    msg = f"rate_limited tier={tier} limit={limit} remaining={remaining} retry_after={retry_after}"
    for key, value in (extra or {}).items():
        msg += f" {key}={value}"
    if detail:
        msg += f" | {detail}"
    return msg


# ---------------------------------------------------------------------------
# Tier 1 — HTTP DoS protection via django-ratelimit
# ---------------------------------------------------------------------------

#: Defaults — overridable per-key via ``ApiKey.rate_override_*`` columns
#: so an admin can loosen a single key without bumping the global default.
DEFAULT_WRITE_RATE = "120/m"
DEFAULT_READ_RATE = "300/m"
WORKSPACE_AGG_WRITE_RATE = "1000/m"
IP_FAILED_AUTH_RATE = "10/m"

# ``django-ratelimit`` keys are computed by callable accessors; these
# helpers consolidate the convention so individual routes stay clean.


def _ratelimit_key_apikey_writes(_group: str, request: HttpRequest) -> str:
    api_key: ApiKey = request.auth  # type: ignore[attr-defined]  # set by ApiKeyAuth
    return f"apikey:{api_key.id}:w"


def _ratelimit_key_apikey_reads(_group: str, request: HttpRequest) -> str:
    api_key: ApiKey = request.auth  # type: ignore[attr-defined]
    return f"apikey:{api_key.id}:r"


def _ratelimit_key_workspace_writes(_group: str, request: HttpRequest) -> str:
    api_key: ApiKey = request.auth  # type: ignore[attr-defined]
    return f"ws:{api_key.workspace_id}:w"


def _override_or(api_key: ApiKey, attr: str, default: str) -> str:
    """Pick the per-key override rate or fall back to the default.

    ``is not None`` (rather than ``if override:``) so that an explicit
    ``0`` value is honoured as "0 requests per minute" — admins use
    that to freeze a misbehaving key without revoking it.
    """
    override = getattr(api_key, attr, None)
    if override is not None:
        return f"{int(override)}/m"
    return default


def enforce_http_rate_limits(request: HttpRequest, *, is_write: bool) -> None:
    """Stack the HTTP-level tiers and raise 429 if any trip.

    Centralized here so routers don't each re-import django-ratelimit
    helpers. Per-tier rates honour per-key overrides; the workspace
    aggregate is global.
    """
    api_key: ApiKey = request.auth  # type: ignore[attr-defined]
    tier_rate = (
        _override_or(api_key, "rate_override_writes", DEFAULT_WRITE_RATE)
        if is_write
        else _override_or(api_key, "rate_override_reads", DEFAULT_READ_RATE)
    )
    tier_key = _ratelimit_key_apikey_writes if is_write else _ratelimit_key_apikey_reads
    if is_ratelimited(
        request=request,
        group=f"agent_api:apikey:{'w' if is_write else 'r'}",
        key=tier_key,
        rate=tier_rate,
        increment=True,
    ):
        raise HttpError(
            429,
            _format_quota_message(
                tier="per_key_writes" if is_write else "per_key_reads",
                limit=_parse_rate_num(tier_rate),
                remaining=0,
                retry_after=60,
            ),
        )
    if is_write and is_ratelimited(
        request=request,
        group="agent_api:workspace:w",
        key=_ratelimit_key_workspace_writes,
        rate=WORKSPACE_AGG_WRITE_RATE,
        increment=True,
    ):
        raise HttpError(
            429,
            _format_quota_message(
                tier="per_workspace_writes",
                limit=_parse_rate_num(WORKSPACE_AGG_WRITE_RATE),
                remaining=0,
                retry_after=60,
            ),
        )
    # Global instance cap — optional, env-driven.
    global_cap = getattr(settings, "BB_API_LIMIT", None)
    if global_cap and is_ratelimited(
        request=request,
        group="agent_api:global",
        key=lambda _g, _r: "global",
        rate=f"{int(global_cap)}/m",
        increment=True,
    ):
        raise HttpError(
            429,
            _format_quota_message(
                tier="global",
                limit=int(global_cap),
                remaining=0,
                retry_after=60,
            ),
        )


def _parse_rate_num(rate: str) -> int:
    return int(rate.split("/", 1)[0])


# ---------------------------------------------------------------------------
# Failed-auth IP throttle — special-case, no api_key on request
# ---------------------------------------------------------------------------
#
# We deliberately *don't* use django-ratelimit here. Its semantics are
# "ratelimited iff usage > limit" — which means a rate of "10/m" lets
# 10 requests in AND THEN serves an 11th before short-circuiting on
# the 12th. For a brute-force defense that's an off-by-one we don't
# want: the budget is "10 failed attempts," period.
#
# Instead we drive Django's cache directly: ``cache.add`` seeds the
# bucket with a TTL on the first failure, ``cache.incr`` bumps it on
# subsequent failures (preserving the TTL), and the threshold check
# is the tighter ``count >= limit``. Same response shape on the
# blocked path (401, not 429) so an attacker can't detect the throttle.

_AUTH_FAIL_LIMIT = 10
_AUTH_FAIL_WINDOW_SECONDS = 60


def _auth_fail_cache_key(request: HttpRequest) -> str:
    return f"agent_api:auth_fail:{_client_ip(request) or 'anon'}"


def is_failed_auth_ip_blocked(request: HttpRequest) -> bool:
    """True iff the IP has accumulated ``>= _AUTH_FAIL_LIMIT`` failures
    in the current rolling window. Read-only — no increment.
    """
    return _cache.get(_auth_fail_cache_key(request), 0) >= _AUTH_FAIL_LIMIT


def record_failed_auth(request: HttpRequest) -> None:
    """Increment the failed-auth counter for this IP.

    On the first failure, ``cache.add`` seeds the bucket with the
    window TTL. On subsequent failures, ``cache.incr`` bumps the
    counter without touching the TTL — so the window stays anchored
    at the *first* failure, not the most recent one.
    """
    key = _auth_fail_cache_key(request)
    if not _cache.add(key, 1, _AUTH_FAIL_WINDOW_SECONDS):
        try:
            _cache.incr(key)
        except ValueError:
            # Race: the key's TTL expired between our ``add`` (which
            # returned False because the key existed) and the incr
            # (which now finds nothing). Re-seed.
            _cache.set(key, 1, _AUTH_FAIL_WINDOW_SECONDS)


def _client_ip(request: HttpRequest) -> str | None:
    """Return the originating client IP, honouring proxies safely.

    Codex review flagged: the previous version unconditionally trusted
    the leftmost ``X-Forwarded-For`` value, which a remote client can
    set to any string. That defeats the failed-auth IP throttle (rotate
    XFF per request to escape the per-IP bucket) and lets the attacker
    pin audit-log rows to a victim's IP.

    Hardening: only honour ``X-Forwarded-For`` when the direct
    ``REMOTE_ADDR`` is in ``settings.BB_TRUSTED_PROXIES``. On platforms
    that terminate TLS at a proxy you actually run (Cloudflare, ALB,
    nginx, …), set that list in env config. Otherwise fall back to the
    socket peer — which is the only IP we can vouch for ourselves.
    """
    trusted = set(getattr(settings, "BB_TRUSTED_PROXIES", ()) or ())
    remote = request.META.get("REMOTE_ADDR")
    if trusted and remote in trusted:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            # Per RFC 7239 the rightmost value is the closest proxy to
            # us; we want the originating client, which is the leftmost
            # value that wasn't itself a trusted proxy. Cheapest safe
            # heuristic: take the leftmost untrusted hop.
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            for hop in hops:
                if hop not in trusted:
                    return hop
            # Every hop was a trusted proxy — fall back to remote.
    return remote
