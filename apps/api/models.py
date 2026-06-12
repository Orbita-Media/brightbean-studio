"""API-App-Models: Workspace-gebundene API-Keys für externe Tools."""

import secrets
import uuid

from django.db import models


class WorkspaceAPIKey(models.Model):
    """Ein API-Key, der einen externen Client als Workspace authentifiziert.

    Der Key wird im Klartext gespeichert (64 Hex-Zeichen, unique index) und
    per Header ``Authorization: Bearer <key>`` übermittelt.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "api_workspace_api_key"
        ordering = ["-created_at"]
        verbose_name = "Workspace API Key"
        verbose_name_plural = "Workspace API Keys"

    def __str__(self):
        return f"APIKey({self.name})"

    @classmethod
    def generate_key(cls) -> str:
        """Erzeugt einen neuen, garantiert eindeutigen Key (64 Hex-Zeichen)."""
        while True:
            key = secrets.token_hex(32)
            if not cls.objects.filter(key=key).exists():
                return key
