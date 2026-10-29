"""``/api/v1/media/*`` — upload, list, retrieve media assets (Gap 1 + 1b).

Upload (POST /) is the only way an agent can introduce a binary into a
workspace; every other write endpoint references already-uploaded
``MediaAsset`` UUIDs. Both this router and the cookie-authenticated UI
flow through ``apps.media_library.services.create_asset``, which is the
single chokepoint for MIME sniffing, size validation, and storage-quota
enforcement (Gap 1a).

The list endpoint is built around the *reuse* scenario: the most useful
filters are recency (``-created_at`` default), ``last_used_at``
(annotated, not stored — see ``MediaAssetManager.with_last_used_at``),
and ``processing_status=completed`` so agents never reference an asset
that hasn't finished async processing.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import File, Form, Query, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import (
    claim_idempotency_slot,
    finalize_idempotent_response,
    fingerprint_request,
    log_audit_entry,
    release_idempotent_claim,
)
from apps.api.schemas import (
    BildpruefungIn,
    BildpruefungOffenItem,
    BildpruefungOffenResponse,
    MediaAssetListResponse,
    MediaAssetResponse,
)
from apps.media_library.models import MediaAsset
from apps.media_library.services import ProtectedAssetError, create_asset, purge_asset, restore_asset, trash_asset

router = Router(tags=["media"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_perm(request: HttpRequest, key: str) -> None:
    membership = getattr(request, "workspace_membership", None)
    if membership is None or not membership.effective_permissions.get(key, False):
        raise HttpError(403, f"Permission denied: {key}")


def _visible_assets_qs(request: HttpRequest):
    """Workspace-scoped assets plus org-shared (workspace_id IS NULL)."""
    workspace = request.workspace  # type: ignore[attr-defined]  # set by ApiKeyAuth
    return MediaAsset.objects.for_workspace_with_shared(
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
    )


def _trashed_assets_qs(request: HttpRequest):
    """Trashed assets of the key's workspace (shared org assets are never trashed via a workspace key)."""
    workspace = request.workspace  # type: ignore[attr-defined]  # set by ApiKeyAuth
    return MediaAsset.all_objects.filter(workspace_id=workspace.id, deleted_at__isnull=False)


def _parse_tags_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# POST /api/v1/media/  (Gap 1)
# ---------------------------------------------------------------------------


@router.post(
    "/",
    response={201: MediaAssetResponse},
    summary="Upload a new media asset (multipart/form-data)",
)
def upload(
    request,
    file: UploadedFile = File(...),
    alt_text: str = Form(""),
    title: str = Form(""),
    folder_id: uuid.UUID | None = Form(None),
    tags: str = Form(""),
    idempotency_key: str | None = Form(None),
):
    """Upload a binary into the workspace's media library.

    Body is ``multipart/form-data`` because agents posting videos can't
    cleanly base64-encode 100 MB into JSON. MCP gets a separate
    ``upload_media`` tool that accepts base64 for small files (≤5 MB).

    ``alt_text`` / ``title`` / ``folder_id`` / ``tags`` are optional and
    persisted alongside the asset, saving the agent a follow-up PATCH.
    ``tags`` is a CSV ("hero,launch") because multipart can't carry JSON
    arrays cleanly.
    """
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "upload_media")

    # Idempotency: hash the relevant params (NOT the file body — too
    # large, and a retry sending the same key with the same file is the
    # common case anyway).
    fp_payload = {
        "filename": file.name,
        "size": file.size,
        "alt_text": alt_text,
        "title": title,
        "folder_id": str(folder_id) if folder_id else None,
        "tags": tags,
    }
    fingerprint = fingerprint_request(request.method or "POST", request.path, fp_payload)
    effective_key = idempotency_key or request.headers.get("Idempotency-Key") or None
    try:
        disposition, replay_status, replay_body = claim_idempotency_slot(
            api_key=request.api_key,
            idempotency_key=effective_key,
            fingerprint=fingerprint,
        )
    except ValueError as exc:
        raise HttpError(422, str(exc)) from exc
    if disposition == "replay":
        return replay_status, replay_body
    if disposition == "in_flight":
        raise HttpError(
            409,
            "An identical upload with this idempotency_key is still in flight; retry shortly.",
        )

    workspace = request.workspace
    folder = None
    if folder_id is not None:
        from apps.media_library.models import MediaFolder

        folder = get_object_or_404(
            MediaFolder.objects.filter(
                organization=workspace.organization,
            ),
            id=folder_id,
        )

    try:
        asset = create_asset(
            organization=workspace.organization,
            workspace=workspace,
            uploaded_file=file,
            uploaded_by=request.user if not request.user.is_anonymous else None,
            folder=folder,
            alt_text=alt_text,
            title=title,
            tags=_parse_tags_csv(tags),
        )
    except ValidationError as exc:
        release_idempotent_claim(api_key=request.api_key, idempotency_key=effective_key)
        raise HttpError(422, _flatten_validation_error(exc)) from exc
    except Exception:
        release_idempotent_claim(api_key=request.api_key, idempotency_key=effective_key)
        raise

    # Mirror the cookie-auth UI's behavior: queue async processing for
    # thumbnails, dimensions, duration, and platform-specific variants.
    # See [apps/media_library/views.py:162]. Without this, processing_status
    # stays at ``pending`` forever and dependent fields stay at their zero
    # defaults.
    from apps.media_library.tasks import process_media_asset

    process_media_asset(str(asset.id))

    body = MediaAssetResponse.from_asset(asset)
    status_code = 201
    log_audit_entry(
        request,
        action=f"media.upload.{status_code}",
        target_id=asset.id,
        status_code=status_code,
    )
    finalize_idempotent_response(
        api_key=request.api_key,
        idempotency_key=effective_key,
        status_code=status_code,
        body=body.model_dump(mode="json"),
    )
    return status_code, body


def _flatten_validation_error(exc: ValidationError) -> str:
    """Render Django's nested ValidationError as a single readable line."""
    if hasattr(exc, "message_dict"):
        flat: list[str] = []
        for field, errors in exc.message_dict.items():
            joined = "; ".join(str(e) for e in errors)
            flat.append(f"{field}: {joined}")
        return " | ".join(flat)
    if hasattr(exc, "messages"):
        return "; ".join(exc.messages)
    return str(exc)


# ---------------------------------------------------------------------------
# Bildprüfung (docs/BILDPRUEFUNG.md). Vor ``/{media_id}`` registriert, damit
# ``bildpruefung`` nie als Asset-ID gelesen wird.
# ---------------------------------------------------------------------------


@router.get(
    "/bildpruefung/offen/",
    response=BildpruefungOffenResponse,
    summary="Medien an künftigen Beiträgen ohne gültige Bildprüfung",
)
def bildpruefung_offen(
    request,
    bis: str | None = Query(None, description="ISO-Zeitpunkt: nur Beiträge mit Termin bis dahin."),
    workspace: uuid.UUID | None = Query(None, description="Workspace-ID; muss der des API-Keys entsprechen."),
    status: str = Query("offen", description="``offen`` (Standard) oder ``durchgefallen``."),
    limit: int = Query(1000, ge=1, le=5000),
):
    """Bilder und Videos an Beiträgen, die noch veröffentlicht werden können, ohne gültige Prüfung.

    Gültig ist eine Prüfung nur, wenn ihr ``sha256`` zur aktuellen Datei passt;
    ``grund`` sagt, warum ein Medium offen ist (``fehlt``, ``veraltet``,
    ``datei_fehlt``). Sortiert nach nächstem Termin, Entwürfe ohne Termin
    zuletzt (mit ``bis`` fallen sie heraus). ``status=durchgefallen`` listet
    stattdessen die gültig durchgefallenen Medien, die neu gemacht werden müssen.
    """
    from django.utils.dateparse import parse_datetime

    from apps.media_library.bildpruefung import offene_medien, pflicht_an

    enforce_http_rate_limits(request, is_write=False)
    key_workspace = request.workspace  # type: ignore[attr-defined]
    if workspace is not None and workspace != key_workspace.id:
        raise HttpError(403, f"Der API-Key gehört zu Workspace {key_workspace.id}, nicht zu {workspace}.")
    if status not in ("offen", "durchgefallen"):
        raise HttpError(422, "status muss offen oder durchgefallen sein.")
    bis_dt = None
    if bis:
        bis_dt = parse_datetime(bis)
        if bis_dt is None:
            raise HttpError(422, "bis muss ein ISO-Zeitpunkt sein, z. B. 2026-11-12T00:00:00Z.")
        if bis_dt.tzinfo is None:
            bis_dt = bis_dt.replace(tzinfo=UTC)

    ergebnis = offene_medien(workspace_ids=[key_workspace.id], bis=bis_dt)
    eintraege = ergebnis["eintraege"] if status == "offen" else ergebnis["durchgefallen"]
    # ``sha256`` ist null, solange er nicht gerechnet ist (die Liste lädt keine
    # Dateien); ``manage.py bildpruefung_offen --sha-rechnen`` füllt ihn nach.
    body = BildpruefungOffenResponse(
        anzahl=len(eintraege),
        durchgefallen_anzahl=len(ergebnis["durchgefallen"]),
        pflicht_an=pflicht_an(),
        status=status,
        bis=bis_dt,
        workspace_id=key_workspace.id,
        items=[BildpruefungOffenItem.from_eintrag(e) for e in eintraege[:limit]],
    )
    log_audit_entry(request, action="media.bildpruefung.offen.200", target_id=None, status_code=200)
    return body


@router.post(
    "/{media_id}/bildpruefung/",
    response={200: MediaAssetResponse, 409: dict},
    summary="Ergebnis der Bildprüfung für ein Medium speichern",
)
def bildpruefung_speichern(request, media_id: uuid.UUID, payload: BildpruefungIn):
    """Speichert das Prüfobjekt am Medium.

    ``409``, wenn ``sha256`` nicht zur aktuellen Datei passt (die Antwort
    nennt den aktuellen Wert in ``sha256_aktuell``). ``422`` bei verletztem
    Vertrag (z. B. ``durchgefallen`` ohne Befund, ``von_hand_freigegeben``
    ohne Notiz). Braucht die Berechtigung ``edit_media``; eine Freigabe von
    Hand zusätzlich einen Key, dessen Aussteller Owner/Admin ist.
    """
    from apps.media_library.bildpruefung import (
        VON_HAND_FREIGEGEBEN,
        ShaAbweichung,
        UngueltigePruefung,
        darf_von_hand_freigeben,
        haengt_an_beitraegen,
        pruefung_speichern,
    )

    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "edit_media")
    asset = get_object_or_404(_visible_assets_qs(request), id=media_id)
    if asset.workspace_id is None and not (
        # Org-geteiltes Asset: ein Workspace-Key bewertet es nur, wenn es an
        # einem Beitrag seines Workspace hängt (die Bildwache muss alles prüfen
        # können, was dort veröffentlicht wird) und der Key Medien verwalten darf.
        haengt_an_beitraegen(asset, request.workspace)
        and getattr(request, "workspace_membership", None) is not None
        and request.workspace_membership.effective_permissions.get("manage_media", False)
    ):
        raise HttpError(
            403,
            "Org-geteilte Medien bewertet ein Workspace-Key nur, wenn sie an einem Beitrag dieses "
            "Workspace hängen und der Key die Berechtigung manage_media hat.",
        )
    if payload.status == VON_HAND_FREIGEGEBEN and not darf_von_hand_freigeben(request.user, request.workspace, asset):
        raise HttpError(403, "Von Hand freigeben dürfen nur Owner und Admins.")
    try:
        pruefung_speichern(asset, payload.model_dump())
    except UngueltigePruefung as exc:
        raise HttpError(422, str(exc)) from exc
    except ShaAbweichung as exc:
        log_audit_entry(request, action="media.bildpruefung.409", target_id=asset.id, status_code=409)
        return 409, {
            "error": "conflict",
            "detail": str(exc),
            "sha256_gemeldet": exc.gemeldet,
            "sha256_aktuell": exc.aktuell,
        }
    log_audit_entry(request, action="media.bildpruefung.200", target_id=asset.id, status_code=200)
    asset = MediaAsset.objects.with_last_used_at(_visible_assets_qs(request)).get(id=asset.id)
    return 200, MediaAssetResponse.from_asset(asset, last_used_at=getattr(asset, "last_used_at", None))


# ---------------------------------------------------------------------------
# GET /api/v1/media/{id}  (Gap 1 + 1b)
# ---------------------------------------------------------------------------


@router.get("/{media_id}", response=MediaAssetResponse, summary="Retrieve a single media asset")
def retrieve(request, media_id: uuid.UUID):
    enforce_http_rate_limits(request, is_write=False)
    qs = MediaAsset.objects.with_last_used_at(_visible_assets_qs(request))
    asset = get_object_or_404(qs, id=media_id)
    log_audit_entry(request, action="media.read.200", target_id=asset.id, status_code=200)
    return MediaAssetResponse.from_asset(asset, last_used_at=getattr(asset, "last_used_at", None))


# ---------------------------------------------------------------------------
# GET /api/v1/media/  (Gap 1b)
# ---------------------------------------------------------------------------


_ORDER_BY_WHITELIST = {
    "-created_at",
    "created_at",
    "-updated_at",
    "updated_at",
    "-last_used_at",
    "last_used_at",
}

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode() + b"==")
        return json.loads(raw.decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise HttpError(422, "Invalid cursor.") from exc


def _encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, default=str).encode()).rstrip(b"=").decode()


@router.get(
    "/",
    response=MediaAssetListResponse,
    summary="List media assets — recency-first, with filters for the reuse case",
)
def list_media(
    request,
    q: str | None = Query(None, description="Substring search on filename + tags."),
    media_type: str | None = Query(None, description="image | video | gif | document"),
    tags: str | None = Query(None, description="Comma-separated tags; ALL must match."),
    folder_id: uuid.UUID | None = Query(None),
    is_starred: bool | None = Query(None),
    processing_status: str = Query(
        "completed",
        description=(
            "Default ``completed`` so agents never reference half-processed assets. "
            "Set to ``any`` to include in-flight uploads."
        ),
    ),
    created_after: str | None = Query(None),
    created_before: str | None = Query(None),
    trashed: bool = Query(False, description="``true`` lists the trash instead of the library."),
    order_by: str = Query("-created_at"),
    cursor: str | None = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
):
    enforce_http_rate_limits(request, is_write=False)

    if order_by not in _ORDER_BY_WHITELIST:
        raise HttpError(422, f"order_by must be one of {sorted(_ORDER_BY_WHITELIST)}.")

    qs = MediaAsset.objects.with_last_used_at(_trashed_assets_qs(request) if trashed else _visible_assets_qs(request))

    if processing_status and processing_status.lower() != "any":
        qs = qs.filter(processing_status=processing_status)
    if media_type:
        qs = qs.filter(media_type=media_type)
    if folder_id is not None:
        qs = qs.filter(folder_id=folder_id)
    if is_starred is not None:
        qs = qs.filter(is_starred=is_starred)
    for tag in _parse_tags_csv(tags):
        qs = qs.filter(tags__contains=[tag])
    if created_after:
        qs = qs.filter(created_at__gte=created_after)
    if created_before:
        qs = qs.filter(created_at__lte=created_before)
    if q:
        qs = MediaAsset.objects.search(q, queryset=qs)

    qs = qs.order_by(order_by, "id")

    # Simple offset cursor — we encode an integer offset rather than a
    # composite (value, id) tuple because cursor stability across reorder
    # isn't a stated requirement and offset paginates correctly with the
    # stable ``order_by(..., "id")`` tiebreak.
    cursor_payload = _decode_cursor(cursor)
    offset = int(cursor_payload.get("o", 0)) if cursor_payload else 0
    items_qs = qs[offset : offset + limit + 1]
    rows = list(items_qs)
    has_more = len(rows) > limit
    rows = rows[:limit]

    body = MediaAssetListResponse(
        items=[MediaAssetResponse.from_asset(a, last_used_at=getattr(a, "last_used_at", None)) for a in rows],
        next_cursor=_encode_cursor({"o": offset + limit}) if has_more else None,
        limit=limit,
    )
    log_audit_entry(request, action="media.read.200", target_id=None, status_code=200)
    return body


# ---------------------------------------------------------------------------
# DELETE /api/v1/media/{id} + POST /api/v1/media/{id}/restore  (trash)
# ---------------------------------------------------------------------------


@router.delete(
    "/{media_id}",
    response={200: MediaAssetResponse, 204: None},
    summary="Move a media asset to the trash; with ?permanent=true delete a trashed asset for good",
)
def delete(request, media_id: uuid.UUID, permanent: bool = Query(False)):
    """Trash first, purge second.

    Without ``permanent`` the asset goes to the trash: it disappears from the
    library and the list, the file stays, ``POST /media/{id}/restore`` brings
    it back. ``409`` if a scheduled or publishing post still uses it.
    Shared org assets cannot be deleted with a workspace key (``403``).

    ``permanent=true`` only works on an asset already in the trash (``409``
    otherwise) and removes file, thumbnail and versions.
    """
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "delete_media")
    if permanent:
        asset = _trashed_assets_qs(request).filter(id=media_id).first()
        if asset is None:
            get_object_or_404(_visible_assets_qs(request), id=media_id)
            raise HttpError(409, "Move the asset to the trash first (DELETE without permanent).")
        purge_asset(asset)
        log_audit_entry(request, action="media.purge", target_id=media_id, status_code=204)
        return 204, None
    asset = get_object_or_404(_visible_assets_qs(request), id=media_id)
    if asset.workspace_id is None:
        raise HttpError(403, "Shared organization assets cannot be deleted with a workspace key.")
    try:
        trash_asset(asset)
    except ProtectedAssetError as exc:
        raise HttpError(409, f"Asset is used by scheduled posts: {exc.referencing_posts}") from exc
    log_audit_entry(request, action="media.trash", target_id=asset.id, status_code=200)
    return 200, MediaAssetResponse.from_asset(asset)


@router.post("/{media_id}/restore", response=MediaAssetResponse, summary="Restore a media asset from the trash")
def restore(request, media_id: uuid.UUID):
    enforce_http_rate_limits(request, is_write=True)
    _require_perm(request, "delete_media")
    asset = get_object_or_404(_trashed_assets_qs(request), id=media_id)
    restore_asset(asset)
    log_audit_entry(request, action="media.restore", target_id=asset.id, status_code=200)
    return MediaAssetResponse.from_asset(asset)
