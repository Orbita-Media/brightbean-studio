"""Views der externen Content-API (v1).

Alle Endpoints sind über WorkspaceAPIKeyAuthentication geschützt und
strikt auf den Workspace des verwendeten API-Keys beschränkt.
"""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.composer.models import Post
from apps.media_library.models import MediaAsset
from apps.media_library.services import create_asset, extract_image_metadata
from apps.media_library.tasks import process_media_asset
from apps.social_accounts.models import SocialAccount

from .serializers import PostCreateSerializer, serialize_post_detail

logger = logging.getLogger(__name__)


class AccountListView(APIView):
    """GET /api/v1/accounts/ – verbundene Social Accounts des Workspace."""

    def get(self, request):
        accounts = SocialAccount.objects.for_workspace(request.workspace.id).order_by("platform", "account_name")
        return Response(
            {
                "accounts": [
                    {
                        "id": str(account.id),
                        "platform": account.platform,
                        "account_name": account.account_name,
                        "account_handle": account.account_handle,
                        "connection_status": account.connection_status,
                    }
                    for account in accounts
                ]
            }
        )


class MediaUploadView(APIView):
    """POST /api/v1/media/ – Multipart-Upload eines Media-Assets."""

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return Response(
                {"detail": "Feld 'file' fehlt (multipart/form-data erforderlich)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workspace = request.workspace
        try:
            asset = create_asset(
                organization=workspace.organization,
                workspace=workspace,
                uploaded_file=uploaded_file,
                uploaded_by=None,
            )
        except DjangoValidationError as exc:
            messages = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": messages}, status=status.HTTP_400_BAD_REQUEST)

        alt_text = request.data.get("alt_text") or ""
        title = request.data.get("title") or ""
        update_fields = []
        if alt_text:
            asset.alt_text = alt_text
            update_fields.append("alt_text")
        if title:
            asset.title = title[:255]
            update_fields.append("title")
        if update_fields:
            asset.save(update_fields=[*update_fields, "updated_at"])

        # Bild-Dimensionen synchron extrahieren, damit width/height direkt
        # in der Response stehen (Videos laufen async über den Worker).
        if asset.media_type in (MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF):
            metadata = extract_image_metadata(asset.file)
            if metadata:
                asset.width = metadata.get("width", 0)
                asset.height = metadata.get("height", 0)
                asset.save(update_fields=["width", "height", "updated_at"])

        # Thumbnail + Video-Metadaten asynchron, wie beim Web-Upload.
        process_media_asset(str(asset.id))

        try:
            url = request.build_absolute_uri(asset.file.url)
        except Exception:
            logger.exception("Konnte URL für Asset %s nicht bestimmen", asset.id)
            url = ""

        return Response(
            {
                "id": str(asset.id),
                "url": url,
                "media_type": asset.media_type,
                "width": asset.width,
                "height": asset.height,
            },
            status=status.HTTP_201_CREATED,
        )


class PostCreateView(APIView):
    """POST /api/v1/posts/ – erstellt Post + PlatformPosts + PostMedia."""

    def post(self, request):
        serializer = PostCreateSerializer(data=request.data, context={"workspace": request.workspace})
        serializer.is_valid(raise_exception=True)
        post = serializer.save()
        return Response(serialize_post_detail(post), status=status.HTTP_201_CREATED)


class PostDetailView(APIView):
    """GET /api/v1/posts/<uuid>/ – Status-Abfrage eines Posts."""

    def get(self, request, post_id):
        post = (
            Post.objects.for_workspace(request.workspace.id)
            .prefetch_related("platform_posts__social_account", "media_attachments__media_asset")
            .filter(pk=post_id)
            .first()
        )
        if post is None:
            return Response({"detail": "Post nicht gefunden."}, status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_post_detail(post))
