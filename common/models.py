import uuid

from django.db import models


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SmsStatus(models.TextChoices):
    SKIPPED = "skipped", "Skipped (sending is switched off)"
    SENT = "sent", "Sent to the provider"
    FAILED = "failed", "Failed"


class SmsDelivery(models.Model):
    """One row per SMS the platform tried to send. The message body is not
    kept, because it often holds a one time code; only its purpose is."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    to_last_digits = models.CharField(max_length=4)
    purpose = models.CharField(max_length=60, db_index=True)
    provider = models.CharField(max_length=30, default="termii")
    status = models.CharField(max_length=10, choices=SmsStatus.choices)
    provider_message_id = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
