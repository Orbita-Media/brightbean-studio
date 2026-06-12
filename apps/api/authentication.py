"""DRF-Authentifizierung über Workspace-API-Keys (Bearer-Token)."""

from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from .models import WorkspaceAPIKey


class APIKeyUser:
    """Minimales User-Objekt für API-Key-Clients (kein Django-User).

    Erfüllt das Interface, das DRF-Permissions wie ``IsAuthenticated``
    erwarten, ohne einen echten Datenbank-User zu benötigen.
    """

    is_authenticated = True
    is_active = True
    is_anonymous = False
    is_staff = False
    is_superuser = False

    def __init__(self, api_key: WorkspaceAPIKey):
        self.api_key = api_key
        self.workspace = api_key.workspace
        self.pk = None

    def __str__(self):
        return f"api-key:{self.api_key.name}"


class WorkspaceAPIKeyAuthentication(BaseAuthentication):
    """Authentifiziert Requests über ``Authorization: Bearer <64-hex-key>``.

    Bei Erfolg:
        ``request.user``      = :class:`APIKeyUser`
        ``request.auth``      = :class:`WorkspaceAPIKey`
        ``request.workspace`` = zugehöriger Workspace
    """

    keyword = "Bearer"

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None
        if len(auth) == 1:
            raise exceptions.AuthenticationFailed("Authorization-Header unvollständig: API-Key fehlt.")
        if len(auth) > 2:
            raise exceptions.AuthenticationFailed("Authorization-Header ungültig: Key darf keine Leerzeichen enthalten.")
        try:
            key = auth[1].decode()
        except UnicodeError:
            raise exceptions.AuthenticationFailed("Authorization-Header enthält ungültige Zeichen.")
        return self._authenticate_key(request, key)

    def _authenticate_key(self, request, key):
        try:
            api_key = WorkspaceAPIKey.objects.select_related("workspace__organization").get(
                key=key,
                is_active=True,
                workspace__is_archived=False,
            )
        except WorkspaceAPIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Ungültiger oder inaktiver API-Key.")

        WorkspaceAPIKey.objects.filter(pk=api_key.pk).update(last_used_at=timezone.now())
        request.workspace = api_key.workspace
        return (APIKeyUser(api_key), api_key)

    def authenticate_header(self, request):
        # Sorgt für 401 (statt 403) bei fehlender/ungültiger Authentifizierung.
        return 'Bearer realm="api"'
