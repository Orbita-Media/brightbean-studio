"""Rate-Limiting für die externe Content-API (pro API-Key)."""

from rest_framework.throttling import SimpleRateThrottle

from .models import WorkspaceAPIKey


class APIKeyRateThrottle(SimpleRateThrottle):
    """Drosselt Requests pro API-Key (Fallback: pro Client-IP).

    Rate wird über ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["api_key"]``
    konfiguriert (aktuell 120/min).
    """

    scope = "api_key"

    def get_cache_key(self, request, view):
        if isinstance(request.auth, WorkspaceAPIKey):
            ident = str(request.auth.pk)
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
