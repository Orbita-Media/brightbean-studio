from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    verbose_name = "Common"

    def ready(self):
        # Registriert die System-Checks zur Medien-Auslieferung (MEDIA-01).
        from . import checks  # noqa: F401
