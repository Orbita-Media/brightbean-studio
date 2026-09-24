"""``/api/v1/posts/*`` — create, read, update, schedule, cancel.

Every write path:

1. Enforces HTTP-level rate limits (per-key, per-workspace, global).
2. Replays the response if an idempotency key matches an earlier request.
3. Checks workspace permission via the shared ``@require_permission`` helper.
4. Validates the target ``SocialAccount`` is in the key's allowlist.
5. Checks per-platform 24h quota.
6. Calls the composer service layer (single source of truth for state).
7. Writes an audit log row.
8. Persists the response under the idempotency key if one was passed.

This ordering means an expensive operation (the create) can't run until
both the cheap rate-limit check and the issuer-permission check pass —
defence against spending DB cycles for unauthorized callers.
"""

from __future__ import annotations

import uuid

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError

from apps.api.limits import check_platform_quota, enforce_http_rate_limits
from apps.api.middleware import (
    claim_idempotency_slot,
    finalize_idempotent_response,
    fingerprint_request,
    log_audit_entry,
    release_idempotent_claim,
)
from apps.api.schemas import (
    CoverRequest,
    CoverResponse,
    CreatePostRequest,
    PostResponse,
    ScheduleRequest,
    UpdatePostRequest,
)
from apps.composer.models import Post
from apps.composer.services import (
    create_post,
    sync_post_scheduled_at,
    transition_platform_post,
)
from apps.social_accounts.models import SocialAccount

router = Router(tags=["posts"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_perm(request: HttpRequest, key: str) -> None:
    """Re-check a workspace permission inside a Ninja route body.

    We can't decorate Ninja routes with ``@require_permission`` because it
    expects a Django view signature; instead we inline the same check
    against the virtual membership shim.
    """
    membership = getattr(request, "workspace_membership", None)
    if membership is None or not membership.effective_permissions.get(key, False):
        raise HttpError(403, f"Permission denied: {key}")


def _resolve_account(request: HttpRequest, social_account_id: uuid.UUID) -> SocialAccount:
    """Resolve the target account and verify it is in the key's allowlist.

    Defence against confused-deputy attacks: even though the bearer is
    valid, the caller may only act on accounts the issuer explicitly
    listed at issuance time.
    """
    api_key = request.api_key  # type: ignore[attr-defined]  # set by ApiKeyAuth
    allowlist_ids = {sa.id for sa in api_key.social_accounts.all()}
    if social_account_id not in allowlist_ids:
        raise HttpError(403, "SocialAccount is not in this key's allowlist.")
    return SocialAccount.objects.get(id=social_account_id)


def _can_view_internal_notes(request: HttpRequest) -> bool:
    """Whether this caller may see a post's team-only ``internal_notes``.

    The composer hides ``internal_notes`` from the ``client`` / ``viewer``
    workspace roles ([views.py](apps/composer/views.py): ``can_view_internal_notes``).
    Those are exactly the builtin roles without ``create_posts`` (see
    ``BUILTIN_ROLE_PERMISSIONS``), so gating visibility on ``create_posts``
    reproduces that rule uniformly for both API keys and OAuth members. Reads
    (`GET /posts/{id}`) aren't otherwise permission-gated, so without this the
    shared serializer would leak notes to any read-capable client/viewer.
    """
    membership = getattr(request, "workspace_membership", None)
    return bool(membership and membership.effective_permissions.get("create_posts", False))


def _post_to_response(request: HttpRequest, post: Post) -> PostResponse:
    return PostResponse.from_post(post, include_internal_notes=_can_view_internal_notes(request))


def _get_workspace_post(request: HttpRequest, post_id: uuid.UUID) -> Post:
    """Fetch a Post that belongs to the key's workspace **and** whose every
    ``PlatformPost`` child targets a ``SocialAccount`` in the key's
    allowlist.

    The workspace filter alone is not enough — a key scoped to LinkedIn-A
    could otherwise read or mutate a Post whose only child is for
    Twitter-B in the same workspace if it happened to know the Post UUID.
    The "all children in allowlist" rule (rather than "any child") means
    schedule/cancel/update can freely iterate ``post.platform_posts``
    without us having to scope each operation sub-Post — there is no
    foreign child for them to touch.

    We intentionally don't distinguish "doesn't exist" from "exists in
    another workspace" from "exists but partially out of scope" — all
    three return 404 so the API doesn't leak the existence of foreign
    IDs to a partial-scope bearer.
    """
    from django.http import Http404

    post = get_object_or_404(
        Post.objects.prefetch_related("platform_posts__social_account"),
        id=post_id,
        workspace_id=request.api_key.workspace_id,  # type: ignore[attr-defined]
    )
    allowed_ids = {sa.id for sa in request.api_key.social_accounts.all()}  # type: ignore[attr-defined]
    pp_account_ids = {pp.social_account_id for pp in post.platform_posts.all()}
    # No platform_posts → nothing this key could legitimately act on.
    # Foreign child → leaking even via a read would be a confused-deputy.
    if not pp_account_ids or not pp_account_ids.issubset(allowed_ids):
        raise Http404()
    return post


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


#: Both ways to reach an Instagram account can publish a trial reel
#: ("trial_params" is documented for /{ig-user-id}/media and /me/media).
TRIAL_PLATFORMS = ("instagram", "instagram_login")


def _check_trial_platform(platform: str) -> None:
    if platform not in TRIAL_PLATFORMS:
        raise HttpError(
            422,
            f"trial is only valid for Instagram accounts; this account is on {platform}.",
        )


def _check_trial_media(assets: list) -> None:
    """A trial reel is a reel: exactly one video.

    Meta: "The media_type must be REELS if this parameter is included". An
    image, a story or a carousel with the switch on would otherwise fail at
    publish time, hours later and without anyone watching. No media yet is
    fine – the video may be attached afterwards.
    """
    if not assets:
        return
    if len(assets) != 1 or not getattr(assets[0], "is_video", False):
        raise HttpError(
            422,
            (
                "trial (Test-Reel) needs exactly one video, because Instagram only accepts "
                f"trial_params on a REELS container; this post has {len(assets)} media item(s)"
                + ("" if len(assets) != 1 else " and it is not a video")
                + "."
            ),
        )


def _trial_extra(extra: dict, ov) -> dict:
    """Write ``trial`` / ``trial_graduation`` of one override into *extra*."""
    from providers.instagram_trial import DEFAULT_GRADUATION, EXTRA_GRADUATION, EXTRA_TRIAL

    extra = dict(extra or {})
    if ov.trial:
        extra[EXTRA_TRIAL] = True
        extra[EXTRA_GRADUATION] = ov.trial_graduation or extra.get(EXTRA_GRADUATION) or DEFAULT_GRADUATION
    else:
        extra.pop(EXTRA_TRIAL, None)
        extra.pop(EXTRA_GRADUATION, None)
    return extra


def _resolve_cover_asset(workspace, asset_id):
    """The cover must be an IMAGE asset of this workspace (422 otherwise)."""
    from apps.media_library.models import MediaAsset

    asset = MediaAsset.objects.filter(id=asset_id, workspace=workspace).first()
    if asset is None:
        raise HttpError(422, f"cover_asset_id {asset_id} is not a media asset of this workspace.")
    if asset.media_type != MediaAsset.MediaType.IMAGE:
        raise HttpError(422, f"cover_asset_id must be an image; {asset_id} is a {asset.media_type}.")
    return asset


def _cover_change(obj, gesetzt: set[str], workspace, *, create: bool):
    """Read ``cover_asset_id`` / ``cover_offset_ms`` of one override or request.

    On create, ``null`` means "nothing"; on PATCH and on /cover, a field sent
    as ``null`` removes the stored value and an omitted field keeps it.
    Returns None when neither field was sent.
    """
    from apps.publisher.cover import CoverChange

    asset_id = getattr(obj, "cover_asset_id", None)
    offset = getattr(obj, "cover_offset_ms", None)
    if create:
        set_asset, set_offset = asset_id is not None, offset is not None
    else:
        set_asset, set_offset = "cover_asset_id" in gesetzt, "cover_offset_ms" in gesetzt
    if not (set_asset or set_offset):
        return None
    asset = _resolve_cover_asset(workspace, asset_id) if set_asset and asset_id is not None else None
    return CoverChange(set_asset=set_asset, asset=asset, set_offset=set_offset, offset_ms=offset)


def _check_cover_for_channel(platform: str, change, media_assets: list) -> None:
    """422 when a cover VALUE does not fit this channel or this post's media.

    Only values are judged: removing a value (``null``) is always fine.
    """
    from apps.publisher.cover import CoverChange, asset_problem, unsupported_fields_message

    wanted = CoverChange(
        set_asset=change.asset is not None,
        asset=change.asset,
        set_offset=change.offset_ms is not None,
        offset_ms=change.offset_ms,
    )
    if wanted.is_empty:
        return
    problem = unsupported_fields_message(platform, wanted) or asset_problem(platform, wanted.asset)
    if problem:
        raise HttpError(422, problem)
    _check_cover_media(wanted.offset_ms, media_assets)


def _check_cover_media(offset_ms: int | None, media_assets: list) -> None:
    """A cover needs a video; a frame must lie inside it (when its length is known).

    No media yet is fine – the video may be attached afterwards.
    """
    from apps.publisher.cover import offset_problem

    videos = [a for a in media_assets if getattr(a, "is_video", False)]
    if media_assets and not videos:
        raise HttpError(422, "Ein Titelbild gibt es nur für Video-Beiträge; dieser Beitrag hat kein Video.")
    problem = offset_problem(offset_ms, videos)
    if problem:
        raise HttpError(422, problem)


def _apply_cover_extra(extra: dict, platform: str, change) -> dict:
    from providers.video_cover import apply_cover_to_extra

    return apply_cover_to_extra(
        extra,
        platform,
        set_asset=change.set_asset,
        asset_id=change.asset.id if change.asset is not None else None,
        set_offset=change.set_offset,
        offset_ms=change.offset_ms,
    )


def _story_conflicts(extra: dict) -> list[str]:
    """Settings in *extra* that a story cannot carry (field names of the API)."""
    from providers.instagram_trial import is_trial
    from providers.video_cover import EXTRA_COVER_ASSET, cover_offset_from_extra

    konflikte: list[str] = []
    if extra.get(EXTRA_COVER_ASSET) or cover_offset_from_extra(extra) is not None:
        konflikte.append("Titelbild (cover_asset_id / cover_offset_ms)")
    if extra.get("collaborators"):
        konflikte.append("Mitwirkende (collaborators)")
    if extra.get("audio_id"):
        konflikte.append("Instagram-Sound (instagram_audio)")
    if is_trial(extra):
        konflikte.append("Test-Reel (trial)")
    return konflikte


def _check_story(platform: str, medien: list, extra: dict) -> None:
    """422 when this channel cannot publish its post as a story.

    A story is exactly one image or one video of the right format and length
    on Instagram or a Facebook page, and it carries none of the reel/feed
    settings. Checked here instead of at publish time, which is 26 hours
    later and without anyone watching.
    """
    from providers.story import STORY_PLATFORMS, story_media_problem

    if platform not in STORY_PLATFORMS:
        raise HttpError(
            422,
            f"post_type 'story' gibt es nur für Instagram und Facebook-Seiten; dieses Konto ist {platform}.",
        )
    problem = story_media_problem(platform, medien)
    if problem:
        raise HttpError(422, problem)
    konflikte = _story_conflicts(extra)
    if konflikte:
        raise HttpError(
            422,
            (
                "Eine Story hat kein(e) "
                + ", ".join(konflikte)
                + ". Im selben Aufruf leeren (null, [] bzw. false) oder post_type weglassen."
            ),
        )


def _story_request_extra(ov) -> dict:
    """What the override of a CREATE request asks for, as ``platform_extra`` keys.

    Only for the conflict check: on create nothing is stored yet, so the sent
    values are the whole state.
    """
    extra: dict = {}
    if ov.cover_asset_id is not None:
        extra["thumbnail_asset_id"] = str(ov.cover_asset_id)
    if ov.cover_offset_ms is not None:
        extra["thumb_offset_ms"] = ov.cover_offset_ms
    if ov.collaborators:
        extra["collaborators"] = list(ov.collaborators)
    if ov.instagram_audio is not None:
        extra["audio_id"] = ov.instagram_audio.audio_id
    if ov.trial:
        extra["trial"] = True
    return extra


@router.post("/", response={201: PostResponse, 200: PostResponse}, summary="Create a draft or scheduled post")
def create(request, payload: CreatePostRequest):
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "create_posts")
    # Scheduling implies "ready to publish without further human review",
    # which is exactly what ``publish_directly`` gates in the composer
    # ([views.py:797](apps/composer/views.py)). The REST API must mirror
    # that contract — a key issued with only ``create_posts`` can park
    # drafts but cannot send them into the publisher's poll loop.
    if payload.action == "schedule":
        _require_perm(request, "publish_directly")

    # ---- Cheap validation runs BEFORE we touch the idempotency table.
    # The motivation: an early failure here (403, 422) would otherwise
    # leave a placeholder claim row that release-on-error has to clean
    # up — and forgetting one release path locks the agent out for 24h.
    # Doing all "can this request possibly succeed?" checks pre-claim
    # keeps the claim/release pair tight and confined to the post-claim
    # path (platform quota + create_post).
    social_account = _resolve_account(request, payload.social_account_id)
    if payload.action == "schedule" and payload.scheduled_at is None:
        raise HttpError(422, "scheduled_at is required when action='schedule'.")

    # Build the platform_overrides dict and validate that each override's
    # social_account_id matches one of the post's target accounts. In the
    # current single-account API that's only ``payload.social_account_id``;
    # anything else would silently no-op at publish time, so we reject up
    # front. The plan's [Gap 2] section calls this out explicitly.
    platform_overrides: dict = {}
    for ov in payload.platform_overrides:
        if ov.social_account_id != payload.social_account_id:
            raise HttpError(
                422,
                (
                    f"platform_overrides[*].social_account_id must match the post's "
                    f"social_account_id ({payload.social_account_id}); got {ov.social_account_id}."
                ),
            )
        override: dict = {
            "title": ov.title,
            "caption": ov.caption,
            "first_comment": ov.first_comment,
        }
        if ov.post_type == "story":
            # Before the other extras: a cover or a trial on a story is a
            # story problem, and the story message is the one that helps.
            from apps.media_library.models import MediaAsset

            wanted = list(payload.media_asset_ids)
            found = {a.id: a for a in MediaAsset.objects.filter(id__in=wanted, workspace=request.api_key.workspace)}
            _check_story(social_account.platform, [found[i] for i in wanted if i in found], _story_request_extra(ov))
        if ov.instagram_audio is not None:
            # A platform sound is an Instagram-only extra. Accepting it for
            # another platform would create a setting that quietly does
            # nothing at publish time, so it is refused here instead.
            if social_account.platform != "instagram":
                raise HttpError(
                    422,
                    (
                        "instagram_audio is only valid for Instagram accounts; "
                        f"this account is on {social_account.platform}."
                    ),
                )
            override["platform_extra"] = {
                "audio_id": ov.instagram_audio.audio_id,
                "audio_volume": ov.instagram_audio.audio_volume,
                "video_volume": ov.instagram_audio.video_volume,
            }
        if ov.collaborators is not None:
            # Same reasoning as the sound above: Instagram is the only channel
            # whose API knows co-authors, so accepting the field elsewhere
            # would store a setting that quietly does nothing at publish time.
            if social_account.platform != "instagram":
                raise HttpError(
                    422,
                    (
                        "collaborators is only valid for Instagram accounts; "
                        f"this account is on {social_account.platform}."
                    ),
                )
            # Normalising here as well as in the provider is deliberate: the
            # stored value is what a human later reads in the composer, and
            # "@name " with a stray space reads like a different name.
            names: list[str] = []
            for raw in ov.collaborators:
                name = str(raw or "").strip().lstrip("@").strip()
                if name and name.lower() not in {n.lower() for n in names}:
                    names.append(name)
            override.setdefault("platform_extra", {})["collaborators"] = names
        if ov.trial is not None:
            _check_trial_platform(social_account.platform)
            if ov.trial:
                from apps.media_library.models import MediaAsset

                wanted = list(payload.media_asset_ids)
                found = {a.id: a for a in MediaAsset.objects.filter(id__in=wanted, workspace=request.api_key.workspace)}
                # Unknown IDs are create_post's job to reject; only judge what exists.
                _check_trial_media([found[i] for i in wanted if i in found])
            override["platform_extra"] = _trial_extra(override.get("platform_extra") or {}, ov)
        cover = _cover_change(ov, set(), request.api_key.workspace, create=True)
        if cover is not None:
            from apps.media_library.models import MediaAsset

            wanted = list(payload.media_asset_ids)
            found = {a.id: a for a in MediaAsset.objects.filter(id__in=wanted, workspace=request.api_key.workspace)}
            _check_cover_for_channel(social_account.platform, cover, [found[i] for i in wanted if i in found])
            override["platform_extra"] = _apply_cover_extra(
                override.get("platform_extra") or {}, social_account.platform, cover
            )
        if ov.post_type == "story":
            from providers.story import apply_story_to_extra

            override["platform_extra"] = apply_story_to_extra(override.get("platform_extra") or {}, True)
        platform_overrides[ov.social_account_id] = override

    # ---- Atomic claim-first idempotency. Three early-out branches
    # before we do *any* mutating work, so concurrent identical retries
    # can never both reach create_post:
    #   replay     — prior request already finished, return its response
    #   in_flight  — a concurrent peer holds the slot, return 409
    #   passthrough/claimed — caller proceeds; only the "claimed" path
    #                         must finalize or release before returning.
    fingerprint = fingerprint_request(request.method or "POST", request.path, payload.dict(by_alias=True))
    # Accept the canonical ``Idempotency-Key`` HTTP header (Stripe-style)
    # in addition to the body field. The header is the industry
    # convention — clients that retry on network timeout typically reuse
    # the header but can't easily re-send the same JSON body — so
    # ignoring it would silently break the "retry-safe" contract Codex
    # review (PR #53) flagged. The body field still wins when both are
    # present, so a deliberate caller can override.
    idempotency_key = payload.idempotency_key or request.headers.get("Idempotency-Key") or None
    try:
        disposition, replay_status, replay_body = claim_idempotency_slot(
            api_key=request.api_key,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc)) from exc
    if disposition == "replay":
        return replay_status, replay_body
    if disposition == "in_flight":
        raise HttpError(
            409,
            "An identical request with this idempotency_key is still in flight; retry shortly.",
        )

    # Platform-quota check has to be inside the claim window because it
    # depends on database state that other concurrent claims could
    # change. Drafts are excluded from the count inside
    # ``check_platform_quota`` so creating drafts cannot exhaust the
    # platform's posting cap.
    if payload.action == "schedule":
        try:
            check_platform_quota(social_account)
        except HttpError:
            release_idempotent_claim(api_key=request.api_key, idempotency_key=idempotency_key)
            raise

    # Single try/except covers every step from create_post through
    # finalize_idempotent_response. Codex review found that earlier
    # code committed the Post + PlatformPost via create_post and then
    # built the response / wrote the audit / called finalize OUTSIDE
    # the release path — so a transient DB error during response build
    # or finalize left the idempotency slot wedged in PENDING forever
    # while the work had already succeeded. Folding everything under
    # one ``try / except / release`` closes that window: any exception
    # after the claim releases the slot, the agent can retry, and the
    # retry will reach create_post fresh (or replay if finalize did
    # commit before the failure).
    try:
        post = create_post(
            workspace=request.api_key.workspace,
            social_account=social_account,
            caption=payload.caption,
            media_asset_ids=payload.media_asset_ids,
            title=payload.title,
            first_comment=payload.first_comment,
            internal_notes=payload.internal_notes,
            scheduled_at=payload.scheduled_at,
            # A scheduled post carries a real time, not a proposal — ignore any
            # proposed_publish_at when scheduling so the two never coexist.
            proposed_publish_at=None if payload.action == "schedule" else payload.proposed_publish_at,
            author=request.user if not request.user.is_anonymous else None,
            status="scheduled" if payload.action == "schedule" else "draft",
            platform_overrides=platform_overrides,
        )
        body = _post_to_response(request, post)
        status_code = 201
        log_audit_entry(
            request,
            action=f"post.create.{payload.action}",
            target_id=post.id,
            status_code=status_code,
        )
        # ``model_dump(mode='json')`` yields JSON-safe primitives
        # (UUID→str, datetime→ISO-8601 str) so the response_body
        # JSONField round-trips cleanly through psycopg's jsonb adapter.
        finalize_idempotent_response(
            api_key=request.api_key,
            idempotency_key=idempotency_key,
            status_code=status_code,
            body=body.model_dump(mode="json"),
        )
    except ValueError as exc:
        # Use the *effective* idempotency key (header fallback applied)
        # so a header-only client's claim is released too — Codex PR #53
        # round-3 flagged that the previous ``payload.idempotency_key``
        # was None on header-only requests and left the claim wedged in
        # PENDING until the 24h sweep.
        release_idempotent_claim(api_key=request.api_key, idempotency_key=idempotency_key)
        raise HttpError(422, str(exc)) from exc
    except Exception:
        release_idempotent_claim(api_key=request.api_key, idempotency_key=idempotency_key)
        raise
    return status_code, body


@router.get("/{post_id}", response=PostResponse, summary="Read a single post")
def retrieve(request, post_id: uuid.UUID):
    enforce_http_rate_limits(request, is_write=False)
    post = _get_workspace_post(request, post_id)
    log_audit_entry(request, action="post.read", target_id=post.id, status_code=200)
    return _post_to_response(request, post)


def _gesetzte_felder(schema) -> set[str]:
    """Welche Felder der Aufrufer WIRKLICH mitgeschickt hat.

    Der Unterschied zwischen "Feld weggelassen" und "Feld mit null" ist hier
    die ganze Frage. Pydantic merkt sich das in ``model_fields_set``; ohne
    diese Unterscheidung wuerde jeder Aenderungsaufruf, der nur den Termin
    verschiebt, nebenbei die kanalspezifische Fassung und die Mitwirkenden
    loeschen - derselbe Fehler, den der Composer bis zum 08.08.2026 hatte.

    Der Rueckfall auf ``__fields_set__`` deckt Pydantic v1 ab, falls das
    Projekt dorthin zurueckgeht; ein leeres Set waere die gefaehrlichste
    Antwort (dann wuerde gar nichts gesetzt), deshalb ist der Rueckfall
    ausdruecklich und nicht stillschweigend.
    """
    felder = getattr(schema, "model_fields_set", None)
    if felder is None:
        felder = getattr(schema, "__fields_set__", None)
    if felder is None:  # pragma: no cover - beide Pydantic-Generationen kennen eines davon
        raise HttpError(500, "Cannot tell which fields were sent (unsupported schema library).")
    return set(felder)


@router.patch("/{post_id}", response=PostResponse, summary="Update draft fields")
def update(request, post_id: uuid.UUID, payload: UpdatePostRequest):
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "create_posts")  # mirrors composer's create-or-edit perm
    post = _get_workspace_post(request, post_id)

    # Only allow updates while editable.
    if not post.is_editable:
        raise HttpError(409, f"Post is not editable in status {post.status}.")

    # Re-timing a scheduled post is publish-budget behaviour: pushing
    # ``scheduled_at`` into the past makes the publisher fire the post on
    # its very next poll (~15 s), and pushing it far into the future buries
    # admin-scheduled content. Either is a privilege escalation for a key
    # that doesn't hold ``publish_directly`` — the create / schedule routes
    # and every MCP transition tool gate this exact mutation on that
    # permission, and the PATCH route must do the same to stay consistent.
    # Codex PR #53 security review (round 4) caught this gap.
    if payload.scheduled_at is not None and post.platform_posts.filter(status="scheduled").exists():
        _require_perm(request, "publish_directly")

    # ---- Validate-everything-first.
    #
    # Codex review found two bugs in the previous implementation:
    #   (a) ``scheduled_children.update(scheduled_at=...)`` ran BEFORE
    #       media validation, so a 422 from a foreign media asset still
    #       committed the new schedule timestamp to the DB.
    #   (b) ``post.media_attachments.all().delete()`` + the create loop
    #       were not wrapped in a transaction, so a mid-loop failure
    #       deleted the originals and persisted only a partial new set.
    # Fix: resolve every required reference and reject every invalid
    # input before any database mutation, then perform all mutations
    # inside a single ``transaction.atomic()`` block. Either everything
    # commits or nothing does.
    from django.db import transaction

    from apps.composer.models import PostMedia
    from apps.media_library.models import MediaAsset

    wanted_media: list = []
    resolved_assets: dict = {}
    if payload.media_asset_ids is not None:
        wanted_media = list(payload.media_asset_ids)
        resolved_assets = {a.id: a for a in MediaAsset.objects.filter(id__in=wanted_media, workspace=post.workspace)}
        missing = [i for i in wanted_media if i not in resolved_assets]
        if missing:
            raise HttpError(422, f"Media asset(s) not in workspace: {missing}")

    # ---- Per-account overrides.
    #
    # ``platform_overrides`` used to be create-only. That gap forced callers
    # into a detour: a draft created without collaborators could never get
    # them, so eight finished drafts would have had to be re-created and the
    # originals deleted by hand. Closing the gap here is cheaper than the
    # detour and does not come back next time.
    #
    # The semantics mirror the composer fix of 2026-08-08 exactly, and the
    # middle case is the whole point:
    #   field absent  -> stored value stays
    #   field present -> stored value is replaced ("" / [] removes it)
    # Reading an absent field as an empty one is what silently deleted a
    # Bluesky caption of 274 characters and let the post fall back to a text
    # that busts the 300-character limit at publish time.
    zu_setzen: list[tuple] = []
    story_geprueft: set = set()
    if payload.platform_overrides is not None:
        kinder = {pp.social_account_id: pp for pp in post.platform_posts.select_related("social_account")}
        for ov in payload.platform_overrides:
            kind = kinder.get(ov.social_account_id)
            if kind is None:
                raise HttpError(
                    422,
                    (
                        "platform_overrides[*].social_account_id must reference an account "
                        f"of this post; {ov.social_account_id} is not one of "
                        f"{sorted(str(k) for k in kinder)}."
                    ),
                )
            gesetzt = _gesetzte_felder(ov)
            namen: list[str] | None = None
            if "collaborators" in gesetzt and ov.collaborators is not None:
                # Same reasoning as on create: Instagram is the only channel
                # whose API knows co-authors, so accepting the field elsewhere
                # would store a setting that quietly does nothing at publish
                # time.
                if kind.social_account.platform != "instagram":
                    raise HttpError(
                        422,
                        (
                            "collaborators is only valid for Instagram accounts; "
                            f"this account is on {kind.social_account.platform}."
                        ),
                    )
                # Normalising here as well as in the provider is deliberate:
                # the stored value is what a human later reads in the composer,
                # and "@name " with a stray space reads like a different name.
                namen = []
                for raw in ov.collaborators:
                    name = str(raw or "").strip().lstrip("@").strip()
                    if name and name.lower() not in {n.lower() for n in namen}:
                        namen.append(name)
            if "trial" in gesetzt and ov.trial is not None:
                _check_trial_platform(kind.social_account.platform)
                if ov.trial:
                    if payload.media_asset_ids is not None:
                        _check_trial_media([resolved_assets[i] for i in wanted_media])
                    else:
                        _check_trial_media(
                            [pm.media_asset for pm in post.media_attachments.select_related("media_asset")]
                        )
            cover = _cover_change(ov, gesetzt, post.workspace, create=False)
            if payload.media_asset_ids is not None:
                medien = [resolved_assets[i] for i in wanted_media]
            else:
                medien = [pm.media_asset for pm in post.media_attachments.select_related("media_asset")]
            story_gesendet = "post_type" in gesetzt
            # The state this channel will have after the request, to judge a
            # story against it: a cover stored earlier conflicts just like
            # one sent now, and clearing it in the same call is the way out.
            from providers.story import apply_story_to_extra, is_story

            simuliert = dict(kind.platform_extra or {})
            if namen is not None:
                if namen:
                    simuliert["collaborators"] = namen
                else:
                    simuliert.pop("collaborators", None)
            if "trial" in gesetzt and ov.trial is not None:
                simuliert = _trial_extra(simuliert, ov)
            if cover is not None:
                simuliert = _apply_cover_extra(simuliert, kind.social_account.platform, cover)
            if story_gesendet:
                simuliert = apply_story_to_extra(simuliert, ov.post_type == "story")
            beruehrt = story_gesendet or payload.media_asset_ids is not None or cover is not None
            beruehrt = beruehrt or ("trial" in gesetzt and bool(ov.trial)) or bool(namen)
            if is_story(simuliert) and beruehrt:
                # Before the cover check: on a story the helpful answer is
                # "a story has no cover", not "a cover needs a video".
                _check_story(kind.social_account.platform, medien, simuliert)
                story_geprueft.add(kind.social_account_id)
            if cover is not None:
                _check_cover_for_channel(kind.social_account.platform, cover, medien)
            zu_setzen.append((kind, gesetzt, ov, namen, cover))

    # New media on a post whose channel is a story (and not named in the
    # overrides above): a second image turns the story into something neither
    # platform can publish, so it is refused now rather than at publish time.
    if payload.media_asset_ids is not None:
        from providers.story import is_story, story_media_problem

        neue_medien = [resolved_assets[i] for i in wanted_media]
        for kind in post.platform_posts.select_related("social_account"):
            if kind.social_account_id in story_geprueft or not is_story(kind.platform_extra):
                continue
            if any(k.social_account_id == kind.social_account_id for k, *_ in zu_setzen):
                # Named in the overrides and switched off there, or judged above.
                continue
            problem = story_media_problem(kind.social_account.platform, neue_medien)
            if problem:
                raise HttpError(422, problem)

    with transaction.atomic():
        update_fields: list[str] = []
        if payload.caption is not None:
            post.caption = payload.caption
            update_fields.append("caption")
        if payload.title is not None:
            post.title = payload.title
            update_fields.append("title")
        if payload.first_comment is not None:
            post.first_comment = payload.first_comment
            update_fields.append("first_comment")
        if payload.internal_notes is not None:
            post.internal_notes = payload.internal_notes
            update_fields.append("internal_notes")
        if payload.scheduled_at is not None:
            # Re-time any currently-scheduled child. Drafts are unaffected.
            # ``QuerySet.update()`` bypasses ``auto_now``, so we include
            # ``updated_at=timezone.now()`` explicitly. Otherwise each
            # child's ``updated_at`` would freeze at its creation time
            # despite an effective state change.
            from django.utils import timezone as _tz

            scheduled_children = post.platform_posts.filter(status="scheduled")
            scheduled_children.update(scheduled_at=payload.scheduled_at, updated_at=_tz.now())
            post.scheduled_at = payload.scheduled_at
            update_fields.append("scheduled_at")
        if payload.proposed_publish_at is not None:
            # Draft-stage suggestion; null is a no-op, consistent with the
            # other PATCH fields. Not gated on publish_directly — it never
            # reaches the publisher.
            post.proposed_publish_at = payload.proposed_publish_at
            update_fields.append("proposed_publish_at")
        if payload.media_asset_ids is not None:
            # Replace the attachment set in order. Validated above, so
            # the only remaining failure modes are DB-level — the atomic
            # block rolls back the whole route if any single
            # ``PostMedia.objects.create`` raises.
            post.media_attachments.all().delete()
            for position, mid in enumerate(wanted_media):
                PostMedia.objects.create(
                    post=post,
                    media_asset=resolved_assets[mid],
                    position=position,
                )

        for kind, gesetzt, ov, namen, cover in zu_setzen:
            kind_fields: list[str] = []
            for feld, spalte in (
                ("title", "platform_specific_title"),
                ("caption", "platform_specific_caption"),
                ("first_comment", "platform_specific_first_comment"),
            ):
                if feld not in gesetzt:
                    continue
                wert = getattr(ov, feld)
                # "" ist eine bewusste Loeschung, None ebenfalls - beide landen
                # als NULL, damit ``effective_*`` wieder auf den Beitrag
                # zurueckfaellt. Ein Leerstring als Override waere ein Beitrag
                # ohne Text auf genau einem Kanal; das will niemand.
                setattr(kind, spalte, wert or None)
                kind_fields.append(spalte)
            if namen is not None:
                extra = dict(kind.platform_extra or {})
                if namen:
                    extra["collaborators"] = namen
                else:
                    extra.pop("collaborators", None)
                kind.platform_extra = extra
                kind_fields.append("platform_extra")
            if "trial" in gesetzt and ov.trial is not None:
                kind.platform_extra = _trial_extra(kind.platform_extra or {}, ov)
                if "platform_extra" not in kind_fields:
                    kind_fields.append("platform_extra")
            if cover is not None:
                kind.platform_extra = _apply_cover_extra(kind.platform_extra or {}, kind.social_account.platform, cover)
                if "platform_extra" not in kind_fields:
                    kind_fields.append("platform_extra")
            if "post_type" in gesetzt:
                from providers.story import apply_story_to_extra

                kind.platform_extra = apply_story_to_extra(kind.platform_extra or {}, ov.post_type == "story")
                if "platform_extra" not in kind_fields:
                    kind_fields.append("platform_extra")
            if kind_fields:
                kind.save(update_fields=[*kind_fields, "updated_at"])

        if update_fields:
            post.save(update_fields=[*update_fields, "updated_at"])

        sync_post_scheduled_at(post)

    post.refresh_from_db()
    log_audit_entry(request, action="post.update", target_id=post.id, status_code=200)
    return _post_to_response(request, post)


@router.post(
    "/{post_id}/cover",
    response=CoverResponse,
    summary="Set the cover image (Titelbild) of a video post – also when scheduled or published",
)
def set_cover(request, post_id: uuid.UUID, payload: CoverRequest):
    """Titelbild for one or all channels of a post, whatever its state.

    * not published yet (draft, scheduled, review …): stored in
      ``platform_extra`` and applied at publish time – date and status stay
      as they are (``result='saved'``),
    * published: YouTube (thumbnails.set) and Facebook
      (``/{video_id}/thumbnails``) change the live video (``'updated'``);
      Instagram and TikTok cannot change a cover after publishing
      (``'unsupported'``),
    * a channel being published right now answers 409 for the whole request.

    Idempotent: the same request twice reports ``'unchanged'`` the second time.
    """
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "create_posts")
    post = _get_workspace_post(request, post_id)

    from apps.composer.models import PlatformPost
    from apps.publisher.cover import LIVE_COVER_PLATFORMS, apply_cover

    change = _cover_change(payload, _gesetzte_felder(payload), post.workspace, create=False)
    if change is None:
        raise HttpError(422, "Send cover_asset_id and/or cover_offset_ms (null removes a stored value).")

    kinder = list(post.platform_posts.select_related("social_account").order_by("created_at"))
    if payload.social_account_id is not None:
        kinder = [k for k in kinder if k.social_account_id == payload.social_account_id]
        if not kinder:
            raise HttpError(422, f"social_account_id {payload.social_account_id} is not a channel of this post.")

    if any(k.status == PlatformPost.Status.PUBLISHING for k in kinder):
        raise HttpError(409, "Der Beitrag wird gerade veröffentlicht; Titelbild danach erneut setzen.")

    medien = [pm.media_asset for pm in post.media_attachments.select_related("media_asset")]
    if payload.social_account_id is not None:
        # One channel named explicitly: a field it cannot use is a caller
        # error, not something to skip quietly.
        _check_cover_for_channel(kinder[0].social_account.platform, change, medien)
        from providers.story import is_story

        if is_story(kinder[0].platform_extra) and (change.asset is not None or change.offset_ms is not None):
            raise HttpError(422, "Dieser Kanal wird als Story veröffentlicht, eine Story hat kein Titelbild.")
    else:
        # All channels: what a channel cannot use is skipped and reported per
        # channel (Bluesky & Co. 'unsupported', a PNG for Instagram 'error').
        _check_cover_media(change.offset_ms, medien)

    # Changing a LIVE video is publish-level power, same gate as scheduling.
    if change.asset is not None and any(
        k.status == PlatformPost.Status.PUBLISHED and k.social_account.platform in LIVE_COVER_PLATFORMS for k in kinder
    ):
        _require_perm(request, "publish_directly")

    results = [apply_cover(kind, change) for kind in kinder]
    log_audit_entry(request, action="post.cover", target_id=post.id, status_code=200)
    return CoverResponse(post_id=post.id, results=results)


@router.post("/{post_id}/schedule", response=PostResponse, summary="Schedule a draft")
def schedule(request, post_id: uuid.UUID, payload: ScheduleRequest):
    enforce_http_rate_limits(request, is_write=True)
    # Same ``publish_directly`` contract as the create-with-schedule
    # branch: only keys that can publish-directly may push a post into
    # the SCHEDULED state. See ``create``.
    _require_perm(request, "create_posts")
    _require_perm(request, "publish_directly")
    post = _get_workspace_post(request, post_id)

    # Schedule every draft child; a single-account key produces one child,
    # but defensively we apply the transition to all draft children so we
    # don't half-schedule.
    drafts = list(post.platform_posts.filter(status="draft"))
    if not drafts:
        raise HttpError(409, "No draft platform posts to schedule.")

    # Quota check is per-account, so we evaluate it once per child before
    # we touch any state. Doing the checks first means an over-quota
    # account fails the whole route with 429 — no partial commit.
    for pp in drafts:
        check_platform_quota(pp.social_account)

    # Wrap the per-child transitions in a single outer atomic so a
    # mid-loop ValueError rolls back any earlier ``scheduled`` commits.
    # Without this, child 1 could be persisted as ``scheduled`` while
    # child 2's ``transition_to`` rejects the move (concurrent admin
    # edit, state-machine conflict) — the route 422s but the post is
    # left in a half-scheduled state.
    from django.db import transaction

    with transaction.atomic():
        for pp in drafts:
            try:
                transition_platform_post(pp, "scheduled", scheduled_at=payload.scheduled_at)
            except ValueError as exc:
                raise HttpError(422, str(exc)) from exc

    post.refresh_from_db()
    log_audit_entry(request, action="post.schedule", target_id=post.id, status_code=200)
    return _post_to_response(request, post)


@router.post("/{post_id}/cancel", response=PostResponse, summary="Cancel a scheduled post (back to draft)")
def cancel(request, post_id: uuid.UUID):
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "create_posts")
    post = _get_workspace_post(request, post_id)

    scheduled_children = list(post.platform_posts.filter(status="scheduled"))
    if not scheduled_children:
        raise HttpError(409, "No scheduled platform posts to cancel.")

    # Same atomic-loop reasoning as ``schedule``: any per-child transition
    # failure rolls back the whole cancellation. Half-cancelled posts
    # would otherwise leave the publisher with a mix of ``draft`` and
    # ``scheduled`` children, which is exactly the inconsistent state
    # the route was supposed to prevent.
    from django.db import transaction

    with transaction.atomic():
        for pp in scheduled_children:
            try:
                transition_platform_post(pp, "draft")
            except ValueError as exc:
                raise HttpError(422, str(exc)) from exc

    post.refresh_from_db()
    log_audit_entry(request, action="post.cancel", target_id=post.id, status_code=200)
    return _post_to_response(request, post)
