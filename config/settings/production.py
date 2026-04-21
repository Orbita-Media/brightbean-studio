from .base import *  # noqa: F401, F403

DEBUG = False

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
