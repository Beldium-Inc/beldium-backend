from django.apps import AppConfig


class MiningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mining"

    def ready(self):
        from mining import signals  # noqa: F401
