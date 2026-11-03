"""The document vault: a backup store for records that partners and the
Beldium desk want kept safe.

Each sector (Quality & Control first) keeps its documents in categories. A
stored document is never changed or removed: a corrected or newer file is a
new version that points to the one it replaces, and every upload is
confirmed by a second person before it counts as approved. Each file carries
a SHA-256 fingerprint taken at upload, so the stored copy can be checked
against it at any time.
"""
import secrets
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel
from compliance.models import Sector


class AppendOnlyError(Exception):
    """Raised when code tries to change or remove an append only record."""


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Vault records cannot be updated. Add a new record instead.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise AppendOnlyError("Vault records cannot be updated. Add a new record instead.")

    def delete(self):
        raise AppendOnlyError("Vault records cannot be deleted.")


class AppendOnlyModel(TimeStampedModel):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Vault records cannot be updated. Add a new record instead.")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Vault records cannot be deleted.")


def vault_reference():
    return f"BLD-VLT-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def vault_upload_path(instance, filename):
    # Files are grouped on storage the same way they are grouped on screen,
    # so a raw copy of the bucket is still readable by a person.
    now = timezone.now()
    owner = str(instance.organisation_id) if instance.organisation_id else "beldium"
    return f"vault/{instance.sector}/{owner}/{instance.category.code}/{now:%Y/%m}/{Path(filename).name}"


class VaultCategory(TimeStampedModel):
    """A folder within a sector. Configuration, not a record, so it may be
    edited; documents point to it and keep it from being removed."""

    sector = models.CharField(max_length=20, choices=Sector.choices, db_index=True)
    code = models.SlugField(max_length=60)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sector", "sort_order", "name"]
        verbose_name_plural = "vault categories"
        constraints = [models.UniqueConstraint(fields=["sector", "code"], name="unique_vault_category_code")]

    def __str__(self):
        return f"{self.get_sector_display()}: {self.name}"


class VaultDocument(AppendOnlyModel):
    reference = models.CharField(max_length=50, unique=True, default=vault_reference, editable=False)
    sector = models.CharField(max_length=20, choices=Sector.choices, db_index=True)
    category = models.ForeignKey(VaultCategory, on_delete=models.PROTECT, related_name="documents")
    # Blank for the Beldium desk's own records.
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, null=True, blank=True,
        related_name="vault_documents",
    )

    # Every version of one document shares a family id. A new version points
    # to the one it replaces; one-to-one keeps the history a single line.
    family = models.UUIDField(default=uuid.uuid4, db_index=True)
    version = models.PositiveIntegerField(default=1)
    supersedes = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    version_note = models.CharField(max_length=255, blank=True)

    file = models.FileField(upload_to=vault_upload_path, max_length=500)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["sector", "organisation", "category"])]

    def __str__(self):
        return f"{self.reference} v{self.version}"

    @property
    def is_latest(self):
        return not hasattr(self, "superseded_by")


class VaultApproval(AppendOnlyModel):
    """The second person's confirmation that an upload is the right record."""

    document = models.OneToOneField(VaultDocument, on_delete=models.PROTECT, related_name="approval")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=255, blank=True)


class VaultAction(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    NEW_VERSION = "new_version", "New version uploaded"
    APPROVED = "approved", "Approved"
    DOWNLOADED = "downloaded", "Downloaded"
    INTEGRITY_CHECKED = "integrity_checked", "Integrity checked"


class VaultActivity(AppendOnlyModel):
    """Who did what to a vault document, including every download."""

    document = models.ForeignKey(VaultDocument, on_delete=models.PROTECT, related_name="activity")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    action = models.CharField(max_length=30, choices=VaultAction.choices)
    detail = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "vault activity"
