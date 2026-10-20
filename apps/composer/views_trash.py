"""Workspace trash: trashed posts and media assets, restore or delete for good.

Deleting in the UI (post delete, media "Move to trash") and over the API
(``DELETE /posts/{id}``, ``DELETE /media/{id}``) only sets ``deleted_at``.
Permanent deletion is the second step and only happens here or with
``?permanent=true`` on the API.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from apps.media_library.models import MediaAsset
from apps.media_library.services import purge_asset, restore_asset
from apps.members.decorators import require_permission

from .models import PlatformPost, Post
from .services import purge_post, restore_post
from .views import _get_workspace


def _trashed_posts(workspace):
    return Post.all_objects.filter(workspace=workspace, deleted_at__isnull=False)


def _trashed_assets(workspace):
    return MediaAsset.all_objects.filter(workspace=workspace, deleted_at__isnull=False)


@login_required
@require_GET
def trash_index(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    posts = list(_trashed_posts(workspace).order_by("-deleted_at")[:200])
    children = {}
    for pp in PlatformPost.all_objects.filter(post__in=posts).select_related("social_account"):
        children.setdefault(pp.post_id, []).append(pp)
    for p in posts:
        p.trash_children = children.get(p.id, [])
    assets = list(_trashed_assets(workspace).order_by("-deleted_at")[:200])
    return render(
        request,
        "composer/trash.html",
        {"workspace": workspace, "posts": posts, "assets": assets, "settings_active": "trash"},
    )


def _row_gone():
    # The row removes itself (hx-swap="outerHTML" with an empty body).
    return HttpResponse("", headers={"HX-Trigger": "trashChanged"})


@login_required
@require_permission("create_posts")
@require_POST
def trash_post_restore(request, workspace_id, post_id):
    workspace = _get_workspace(request, workspace_id)
    restore_post(get_object_or_404(_trashed_posts(workspace), id=post_id))
    return _row_gone()


@login_required
@require_permission("create_posts")
@require_POST
def trash_post_purge(request, workspace_id, post_id):
    workspace = _get_workspace(request, workspace_id)
    purge_post(get_object_or_404(_trashed_posts(workspace), id=post_id))
    return _row_gone()


@login_required
@require_permission("delete_media")
@require_POST
def trash_asset_restore(request, workspace_id, asset_id):
    workspace = _get_workspace(request, workspace_id)
    restore_asset(get_object_or_404(_trashed_assets(workspace), id=asset_id))
    return _row_gone()


@login_required
@require_permission("delete_media")
@require_POST
def trash_asset_purge(request, workspace_id, asset_id):
    workspace = _get_workspace(request, workspace_id)
    purge_asset(get_object_or_404(_trashed_assets(workspace), id=asset_id))
    return _row_gone()
