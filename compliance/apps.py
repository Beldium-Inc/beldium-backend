from django.apps import AppConfig


class ComplianceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "compliance"

    def ready(self):
        from compliance import signals  # noqa: F401
