from django.contrib import admin

from .models import WorkspaceAPIKey


@admin.register(WorkspaceAPIKey)
class WorkspaceAPIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "workspace", "is_active", "created_at", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("name", "workspace__name")
    readonly_fields = ("key", "created_at", "last_used_at")
