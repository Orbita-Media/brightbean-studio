"""System-Checks für die Medien-Auslieferung (MEDIA-01, Upstream-Issue #130).

Instagram, Facebook, Threads, Pinterest und Google Business holen sich das
Medium selbst von einer öffentlich erreichbaren Adresse ab. Die Adresse
entsteht in ``apps/publisher/engine.py`` aus ``asset.file.url``. Es gibt zwei
Konfigurationen, in denen dabei still eine unbrauchbare Adresse herauskommt –
still deshalb, weil erst Meta den Fehler meldet, und zwar mit einer Meldung,
die nach einem Berechtigungsproblem aussieht.

Diese Checks machen genau diese zwei Fälle beim Start sichtbar.
"""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def media_delivery_check(app_configs, **kwargs):
    """Warnt, wenn Medien nicht öffentlich abrufbar ausgeliefert werden."""
    issues = []
    backend = str(getattr(settings, "STORAGE_BACKEND", "local")).lower()

    if backend == "s3":
        # Ohne eigene Domain baut django-storages presignte URLs mit
        # AWS_QUERYSTRING_EXPIRE (eine Stunde). Für geplante Beiträge ist das
        # eine Zeitbombe: die Adresse ist tot, bevor Meta sie abruft.
        if not getattr(settings, "AWS_S3_CUSTOM_DOMAIN", ""):
            issues.append(
                Warning(
                    "S3_CUSTOM_DOMAIN ist nicht gesetzt: Medien-URLs werden signiert "
                    f"und laufen nach {getattr(settings, 'AWS_QUERYSTRING_EXPIRE', 3600)} "
                    "Sekunden ab. Geplante Beiträge schlagen dann bei "
                    "Instagram/Facebook fehl, weil Meta die Datei erst zum "
                    "Veröffentlichungszeitpunkt abholt.",
                    hint="Eine öffentliche R2-Custom-Domain setzen, z. B. "
                    "S3_CUSTOM_DOMAIN=social-cdn.orbita-media.de",
                    id="media.W001",
                )
            )
    elif not settings.DEBUG:
        # Der static()-Helfer in config/urls.py hängt nur unter DEBUG; WhiteNoise
        # bedient ausschliesslich STATIC_ROOT. Ohne DEBUG gibt es also keine Route
        # für MEDIA_URL – genau der Zustand aus Upstream-Issue #130.
        issues.append(
            Warning(
                "STORAGE_BACKEND=local bei DEBUG=False: für MEDIA_URL existiert "
                "keine HTTP-Route (static() hängt nur unter DEBUG, WhiteNoise "
                "bedient nur Static-Dateien). Jede Instagram-/Facebook-"
                "Veröffentlichung schlägt fehl, weil Meta die Datei selbst abruft.",
                hint="STORAGE_BACKEND=s3 mit öffentlichem Objektspeicher setzen "
                "(siehe docs/MEDIA-STORAGE-FIX.md).",
                id="media.W002",
            )
        )

    return issues
