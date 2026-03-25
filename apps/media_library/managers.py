"""Custom managers for media library models."""

from django.db import models
from django.db.models import Q, Value
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector


class MediaAssetManager(models.Manager):
    def for_workspace(self, workspace_id):
        return self.get_queryset().filter(workspace_id=workspace_id)

    def for_org(self, organization_id):
        return self.get_queryset().filter(organization_id=organization_id)

    def for_workspace_with_shared(self, workspace_id, organization_id):
        """Return workspace-scoped assets plus shared org-level assets."""
        return self.get_queryset().filter(
            Q(workspace_id=workspace_id)
            | Q(workspace__isnull=True, organization_id=organization_id)
        )

    def shared_only(self, organization_id):
        """Return only shared org-level assets (workspace is null)."""
        return self.get_queryset().filter(
            organization_id=organization_id,
            workspace__isnull=True,
        )

    def search(self, query, queryset=None):
        """Full-text search on original_filename and tags."""
        qs = queryset if queryset is not None else self.get_queryset()
        if not query:
            return qs

        search_vector = SearchVector("filename", weight="A")
        search_query = SearchQuery(query, search_type="websearch")

        qs = qs.annotate(
            search=search_vector,
            rank=SearchRank(search_vector, search_query),
        ).filter(
            Q(search=search_query) | Q(tags__contains=[query])
        ).order_by("-rank")

        return qs
