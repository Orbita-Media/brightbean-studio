"""Common request middleware for Orbita Social.

- LegacyDomainRedirectMiddleware: permanent 301 from old brightbean.orbita-media.de
  to social.orbita-media.de. Preserves path and query string.
"""
from __future__ import annotations

from django.http import HttpResponsePermanentRedirect


class LegacyDomainRedirectMiddleware:
    """301 redirect requests targeting brightbean.orbita-media.de to social.orbita-media.de.

    Path and query string are preserved. Health check path (/health/) is exempt so
    upstream healthchecks keep working on both hosts.
    """

    LEGACY_HOST = "brightbean.orbita-media.de"
    TARGET_HOST = "social.orbita-media.de"
    EXEMPT_PATHS = ("/health/",)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()
        if host == self.LEGACY_HOST and not any(
            request.path.startswith(p) for p in self.EXEMPT_PATHS
        ):
            qs = request.META.get("QUERY_STRING", "")
            target = f"https://{self.TARGET_HOST}{request.path}"
            if qs:
                target = f"{target}?{qs}"
            return HttpResponsePermanentRedirect(target)
        return self.get_response(request)
