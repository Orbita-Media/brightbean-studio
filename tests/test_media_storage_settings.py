"""Regressionstests für die Medien-Auslieferung (MEDIA-01, Upstream-Issue #130).

Hintergrund: Instagram, Facebook, Threads, Pinterest und Google Business holen
sich das Medium selbst von einer öffentlich erreichbaren Adresse ab. Bis zum
27.07.2026 lief die Produktion mit ``STORAGE_BACKEND=local``; ``config/urls.py``
hängt den ``static()``-Helfer aber nur unter ``DEBUG`` ein und WhiteNoise bedient
ausschliesslich Static-, nicht Media-Dateien. Es gab also keine HTTP-Route für
``/media/…`` und jede Veröffentlichung über diese Provider wäre gescheitert.

Diese Tests sichern die drei Eigenschaften ab, auf denen die Reparatur beruht.
Sie laufen ohne Netzzugriff und ohne echte R2-Zugangsdaten.
"""

import importlib
import os
import sys
from unittest import mock

import pytest


def _load_settings(module_name, env):
    """Lädt ein Settings-Modul frisch mit der angegebenen Umgebung."""
    with mock.patch.dict(os.environ, env, clear=False):
        sys.modules.pop(module_name, None)
        sys.modules.pop("config.settings.base", None)
        module = importlib.import_module(module_name)
        # Frisch importieren, damit ein zuvor geladener Zustand nicht durchschlägt.
        module = importlib.reload(module)
    return module


BASE_ENV = {
    "SECRET_KEY": "test-secret-key",
    "DEBUG": "False",
    "ALLOWED_HOSTS": "example.com",
    "APP_URL": "https://social.orbita-media.de",
    "EMAIL_BACKEND_TYPE": "smtp",
    "REDIS_URL": "",
    "SENTRY_DSN": "",
}

S3_ENV = {
    **BASE_ENV,
    "STORAGE_BACKEND": "s3",
    "S3_ENDPOINT_URL": "https://accountid.r2.cloudflarestorage.com",
    "S3_ACCESS_KEY_ID": "r2-access-key",
    "S3_SECRET_ACCESS_KEY": "r2-secret-key",
    "S3_BUCKET_NAME": "orbita-social-media",
    "S3_CUSTOM_DOMAIN": "social-cdn.orbita-media.de",
    "S3_REGION_NAME": "auto",
}


class TestS3StorageSettings:
    """``STORAGE_BACKEND=s3`` muss den S3-Zweig samt CSP-Eintrag aktivieren."""

    def test_default_storage_is_s3(self):
        settings = _load_settings("config.settings.base", S3_ENV)
        assert settings.STORAGES["default"]["BACKEND"] == "storages.backends.s3boto3.S3Boto3Storage"
        assert settings.AWS_STORAGE_BUCKET_NAME == "orbita-social-media"
        assert settings.AWS_S3_CUSTOM_DOMAIN == "social-cdn.orbita-media.de"

    def test_storage_origin_lands_in_csp(self):
        """Ohne diesen Eintrag blockiert der Browser die Vorschaubilder."""
        settings = _load_settings("config.settings.base", S3_ENV)
        assert "https://social-cdn.orbita-media.de" in settings.CSP_IMG_SRC
        assert "https://social-cdn.orbita-media.de" in settings.CSP_MEDIA_SRC

    def test_local_backend_keeps_filesystem_storage(self):
        settings = _load_settings("config.settings.base", {**BASE_ENV, "STORAGE_BACKEND": "local"})
        assert settings.STORAGES["default"]["BACKEND"] == "django.core.files.storage.FileSystemStorage"
        assert settings.MEDIA_URL == "/media/"


class TestSesCredentialsAreIsolated:
    """MEDIA-01: die R2-Schlüssel dürfen nicht bei Amazon SES landen.

    ``django_ses/conf.py`` liest ``AWS_SES_ACCESS_KEY_ID`` und fällt sonst auf
    ``AWS_ACCESS_KEY_ID`` zurück – genau das Setting, das der S3-Zweig in
    ``base.py`` mit den R2-Zugangsdaten belegt. Ohne die ausdrückliche Zuweisung
    in ``production.py`` würde SES mit dem R2-Token authentifizieren und jeder
    Mailversand scheitern.
    """

    SES_ENV = {
        **S3_ENV,
        "AWS_ACCESS_KEY_ID": "ses-iam-key",
        "AWS_SECRET_ACCESS_KEY": "ses-iam-secret",
        "AWS_DEFAULT_REGION": "eu-north-1",
    }

    def test_ses_uses_its_own_credentials(self):
        settings = _load_settings("config.settings.production", self.SES_ENV)
        assert settings.EMAIL_BACKEND == "django_ses.SESBackend"
        assert settings.AWS_SES_ACCESS_KEY_ID == "ses-iam-key"
        assert settings.AWS_SES_SECRET_ACCESS_KEY == "ses-iam-secret"

    def test_ses_credentials_differ_from_storage_credentials(self):
        settings = _load_settings("config.settings.production", self.SES_ENV)
        # Der S3-Zweig belegt AWS_ACCESS_KEY_ID mit dem R2-Token …
        assert settings.AWS_ACCESS_KEY_ID == "r2-access-key"
        # … SES darf davon nichts abbekommen.
        assert settings.AWS_SES_ACCESS_KEY_ID != settings.AWS_ACCESS_KEY_ID
        assert settings.AWS_SES_SECRET_ACCESS_KEY != settings.AWS_SECRET_ACCESS_KEY


class TestPublicMediaUrl:
    """Die an Meta übergebene URL muss absolut und ohne Signatur sein."""

    def test_url_is_absolute_and_unsigned(self):
        pytest.importorskip("storages")
        from storages.backends.s3 import S3Storage

        storage = S3Storage(
            bucket_name="orbita-social-media",
            custom_domain="social-cdn.orbita-media.de",
            endpoint_url="https://accountid.r2.cloudflarestorage.com",
            access_key="r2-access-key",
            secret_key="r2-secret-key",
            region_name="auto",
            querystring_auth=True,
        )
        url = storage.url("media_library/2026/07/beispiel.jpg")

        assert url == "https://social-cdn.orbita-media.de/media_library/2026/07/beispiel.jpg"
        # Kein "?X-Amz-Signature=…": Meta ruft ohne Anmeldung ab und presignte
        # URLs laufen nach einer Stunde ab. Der publisher hängt genau diese URL
        # in image_url/video_url (apps/publisher/engine.py:369).
        assert "?" not in url
        assert url.startswith("https://")
