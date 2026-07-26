from .base import *  # noqa: F401, F403
from .base import MIDDLEWARE as _BASE_MIDDLEWARE

DEBUG = False

# CLEAN-01: Legacy-Domain-Redirect brightbean.orbita-media.de -> social.orbita-media.de
# Wird ganz früh in der Middleware-Kette aktiv, vor Security-Redirect, damit der
# Redirect auch bei HTTP-Requests korrekt auf HTTPS+neue Domain geht.
MIDDLEWARE = [
    "apps.common.middleware.LegacyDomainRedirectMiddleware",
    *_BASE_MIDDLEWARE,
]

# Security
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REDIRECT_EXEMPT = [r"^health/$"]

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.error": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

# Mail: Amazon SES via django-ses (IAM-Key statt SMTP-Credentials)
# django-ses liest AWS_ACCESS_KEY_ID und AWS_SECRET_ACCESS_KEY automatisch aus ENV.
# Fallback auf SMTP wenn AWS_ACCESS_KEY_ID nicht gesetzt ist (Dev-Schutz).
import os as _os
if _os.environ.get("AWS_ACCESS_KEY_ID"):
    EMAIL_BACKEND = "django_ses.SESBackend"
    AWS_SES_REGION_NAME = _os.environ.get("AWS_DEFAULT_REGION", "eu-north-1")
    AWS_SES_REGION_ENDPOINT = "email.eu-north-1.amazonaws.com"
    # SES-Zugangsdaten ausdrücklich festnageln (MEDIA-01).
    # Seit STORAGE_BACKEND=s3 setzt base.py die Django-Settings
    # AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY auf die R2-Zugangsdaten.
    # django_ses/conf.py greift zuerst auf AWS_SES_ACCESS_KEY_ID zu und fällt
    # sonst genau auf diese beiden Settings zurück – ohne die nächsten zwei
    # Zeilen würde SES also mit dem R2-Token authentifizieren und jeder
    # Mailversand (Einladungen, Passwort-Reset) mit InvalidClientTokenId
    # scheitern. Die SES-Schlüssel kommen weiterhin aus der Umgebung.
    AWS_SES_ACCESS_KEY_ID = _os.environ["AWS_ACCESS_KEY_ID"]
    AWS_SES_SECRET_ACCESS_KEY = _os.environ.get("AWS_SECRET_ACCESS_KEY", "")
