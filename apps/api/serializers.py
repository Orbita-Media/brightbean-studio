"""Serializer der externen Content-API (v1).

Eingabe-Validierung für Post-Erstellung plus Ausgabe-Serialisierung.
Alle Lookups sind strikt auf den Workspace des API-Keys beschränkt.
"""

from django.db import transaction
from rest_framework import serializers

from apps.composer.models import PlatformPost, Post, PostMedia
from apps.composer.services import sync_post_scheduled_at
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount

# Über die API darf nur draft oder scheduled gesetzt werden. published &
# Co. werden ausschließlich von der Publisher-Engine bzw. dem Approval-
# Workflow vergeben.
ALLOWED_PLATFORM_POST_STATUSES = ("draft", "scheduled")


class PlatformPostInputSerializer(serializers.Serializer):
    """Ein Ziel-Account inkl. optionaler Platform-Overrides."""

    social_account_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=ALLOWED_PLATFORM_POST_STATUSES, default="scheduled")
    platform_specific_caption = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None, trim_whitespace=False
    )
    platform_specific_title = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None, trim_whitespace=False
    )
    platform_extra = serializers.DictField(required=False, default=dict)
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class PostMediaInputSerializer(serializers.Serializer):
    """Ein Media-Anhang mit Position im Carousel."""

    media_asset_id = serializers.UUIDField()
    position = serializers.IntegerField(min_value=0, default=0)
    alt_text = serializers.CharField(required=False, allow_blank=True, default="")


class PostCreateSerializer(serializers.Serializer):
    """Validiert und erstellt Post + PlatformPosts + PostMedia atomar."""

    caption = serializers.CharField(allow_blank=True, trim_whitespace=False)
    title = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)
    first_comment = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=False)
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    tags = serializers.ListField(child=serializers.CharField(max_length=100), required=False, default=list)
    platform_posts = PlatformPostInputSerializer(many=True, required=False, default=list)
    media = PostMediaInputSerializer(many=True, required=False, default=list)

    def validate(self, data):
        workspace = self.context["workspace"]
        errors = {}
        self._accounts = {}
        self._assets = {}

        # --- Social Accounts: müssen zum Workspace gehören ---
        pp_items = data.get("platform_posts") or []
        account_ids = [str(item["social_account_id"]) for item in pp_items]
        if len(account_ids) != len(set(account_ids)):
            errors["platform_posts"] = ["Doppelte social_account_id: pro Account ist nur ein PlatformPost erlaubt."]
        elif account_ids:
            accounts = SocialAccount.objects.for_workspace(workspace.id).filter(id__in=account_ids)
            self._accounts = {str(a.id): a for a in accounts}
            missing = [i for i in account_ids if i not in self._accounts]
            if missing:
                errors["platform_posts"] = [
                    "Folgende Social Accounts gehören nicht zu diesem Workspace: " + ", ".join(missing)
                ]

        # --- Media Assets: Workspace-Assets oder geteilte Org-Assets ---
        media_items = data.get("media") or []
        asset_ids = [str(item["media_asset_id"]) for item in media_items]
        if len(asset_ids) != len(set(asset_ids)):
            errors["media"] = ["Doppelte media_asset_id: jedes Asset darf nur einmal angehängt werden."]
        elif asset_ids:
            assets = MediaAsset.objects.for_workspace_with_shared(
                workspace_id=workspace.id,
                organization_id=workspace.organization_id,
            ).filter(id__in=asset_ids)
            self._assets = {str(a.id): a for a in assets}
            missing = [i for i in asset_ids if i not in self._assets]
            if missing:
                errors["media"] = [
                    "Folgende Media Assets gehören nicht zu diesem Workspace: " + ", ".join(missing)
                ]

        # --- Scheduling: status=scheduled braucht einen Zeitpunkt ---
        post_scheduled_at = data.get("scheduled_at")
        scheduling_errors = []
        for idx, item in enumerate(pp_items):
            if item.get("status") == "scheduled" and not item.get("scheduled_at") and not post_scheduled_at:
                scheduling_errors.append(
                    f"platform_posts[{idx}]: status 'scheduled' benötigt scheduled_at "
                    "(am PlatformPost oder auf Post-Ebene)."
                )
        if scheduling_errors:
            errors.setdefault("platform_posts", []).extend(scheduling_errors)

        if errors:
            raise serializers.ValidationError(errors)
        return data

    def create(self, validated_data):
        workspace = self.context["workspace"]
        with transaction.atomic():
            post = Post.objects.create(
                workspace=workspace,
                author=None,
                title=validated_data.get("title") or "",
                caption=validated_data.get("caption") or "",
                first_comment=validated_data.get("first_comment") or "",
                tags=validated_data.get("tags") or [],
                scheduled_at=validated_data.get("scheduled_at"),
            )
            for item in validated_data.get("media") or []:
                PostMedia.objects.create(
                    post=post,
                    media_asset=self._assets[str(item["media_asset_id"])],
                    position=item.get("position", 0),
                    alt_text=item.get("alt_text") or "",
                )
            for item in validated_data.get("platform_posts") or []:
                PlatformPost.objects.create(
                    post=post,
                    social_account=self._accounts[str(item["social_account_id"])],
                    status=item.get("status") or "scheduled",
                    platform_specific_caption=item.get("platform_specific_caption"),
                    platform_specific_title=item.get("platform_specific_title"),
                    platform_extra=item.get("platform_extra") or {},
                    scheduled_at=item.get("scheduled_at"),
                )
            # Post.scheduled_at mit frühester Kind-Zeit synchron halten
            # (gleiches Verhalten wie der Web-Composer).
            sync_post_scheduled_at(post)
        return post


def _iso(dt):
    return dt.isoformat() if dt else None


def serialize_post_detail(post):
    """Detail-Darstellung eines Posts inkl. PlatformPosts und Media."""
    platform_posts = []
    for pp in post.platform_posts.select_related("social_account").all():
        platform_posts.append(
            {
                "id": str(pp.id),
                "social_account_id": str(pp.social_account_id),
                "platform": pp.social_account.platform,
                "account_name": pp.social_account.account_name,
                "status": pp.status,
                "scheduled_at": _iso(pp.scheduled_at or post.scheduled_at),
                "published_at": _iso(pp.published_at),
                "platform_post_id": pp.platform_post_id,
                "publish_error": pp.publish_error,
            }
        )

    media = []
    for pm in post.media_attachments.select_related("media_asset").all():
        media.append(
            {
                "media_asset_id": str(pm.media_asset_id),
                "position": pm.position,
                "alt_text": pm.alt_text,
                "media_type": pm.media_asset.media_type,
                "filename": pm.media_asset.filename,
            }
        )

    return {
        "id": str(post.id),
        "status": post.status,
        "title": post.title,
        "caption": post.caption,
        "first_comment": post.first_comment,
        "tags": post.tags,
        "scheduled_at": _iso(post.scheduled_at),
        "published_at": _iso(post.published_at),
        "created_at": _iso(post.created_at),
        "platform_posts": platform_posts,
        "media": media,
    }
