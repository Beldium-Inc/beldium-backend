import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def application_reference():
    return f"BLD-QA-APP-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def sample_reference():
    return f"BLD-QA-SMP-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def certificate_reference():
    return f"BLD-QA-CERT-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def nc_reference():
    return f"BLD-QA-NCR-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def new_uuid():
    return str(uuid.uuid4())


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "submitted", "Submitted"
    IN_REVIEW = "in_review", "In review"
    INFO_REQUESTED = "info_requested", "Info requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DocStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    FLAGGED = "flagged", "Flagged"
    EXPIRED = "expired", "Expired"


class SampleStatus(models.TextChoices):
    REGISTERED = "registered", "Registered"
    IN_TRANSIT = "in_transit", "In transit"
    RECEIVED = "received", "Received"
    TESTING = "testing", "Testing"
    REVIEWED = "reviewed", "Reviewed"
    CERTIFIED = "certified", "Certified"
    REJECTED = "rejected", "Rejected"


class ResultVerdict(models.TextChoices):
    PASS = "pass", "Pass"
    FAIL = "fail", "Fail"
    CONDITIONAL = "conditional", "Conditional"
    PENDING = "pending", "Pending"


class CertificateStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"
    DRAFT = "draft", "Draft"


class NCSeverity(models.TextChoices):
    MINOR = "minor", "Minor"
    MAJOR = "major", "Major"
    CRITICAL = "critical", "Critical"


class NCStatus(models.TextChoices):
    OPEN = "open", "Open"
    CAPA_SUBMITTED = "capa_submitted", "CAPA submitted"
    CLOSED = "closed", "Closed"


class CAPAStatus(models.TextChoices):
    OPEN = "open", "Open"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETE = "complete", "Complete"


# The nested sub-objects in the frontend contract (documents, risk flags, audit
# trail, custody events, test results, capa items, ...) do not need their own
# relational tables to satisfy the API shape the frontend expects - they are
# always read/written as a whole alongside their parent. They are modelled as
# JSON lists/dicts on the parent record, each item keyed by its own "id" (a
# uuid4 string), matching the `UUID` typed ids in quality.ts exactly.
class QualityApplication(TimeStampedModel):
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quality_applications",
    )
    reference = models.CharField(max_length=50, unique=True, default=application_reference, editable=False)
    status = models.CharField(max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.SUBMITTED, db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    risk_score = models.PositiveSmallIntegerField(default=0)

    organisation_data = models.JSONField(default=dict, blank=True)
    capability_data = models.JSONField(default=dict, blank=True)
    laboratory_data = models.JSONField(default=dict, blank=True)
    documents = models.JSONField(default=list, blank=True)
    risk_flags = models.JSONField(default=list, blank=True)
    audit = models.JSONField(default=list, blank=True)
    decision_note = models.TextField(blank=True)

    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class BuyerSpec(TimeStampedModel):
    name = models.CharField(max_length=255)
    buyer_org = models.CharField(max_length=255, blank=True)
    buyer_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    material = models.CharField(max_length=120, blank=True)
    limits = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Sample(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=sample_reference, editable=False)
    material = models.CharField(max_length=120)
    lot = models.CharField(max_length=120, blank=True)
    mine_site = models.CharField(max_length=200, blank=True)
    origin = models.CharField(max_length=200, blank=True)
    mass_kg = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    registered_at = models.DateTimeField(default=timezone.now)
    miner_org = models.CharField(max_length=255, blank=True)
    partner_org = models.CharField(max_length=255, blank=True)
    buyer_org = models.CharField(max_length=255, blank=True)
    buyer_spec = models.ForeignKey(BuyerSpec, on_delete=models.SET_NULL, null=True, blank=True, related_name="samples")
    status = models.CharField(max_length=20, choices=SampleStatus.choices, default=SampleStatus.REGISTERED, db_index=True)

    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    custody = models.JSONField(default=list, blank=True)
    test_request = models.JSONField(default=dict, blank=True, null=True)
    results = models.JSONField(default=list, blank=True)
    quality_review = models.JSONField(default=dict, blank=True, null=True)
    audit = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class Certificate(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=certificate_reference, editable=False)
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, related_name="certificates")
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.CharField(max_length=255, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=CertificateStatus.choices, default=CertificateStatus.ACTIVE, db_index=True)
    verification_hash = models.CharField(max_length=128, blank=True)
    scans = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class QualityNonConformity(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=nc_reference, editable=False)
    title = models.CharField(max_length=255)
    raised_at = models.DateTimeField(default=timezone.now)
    raised_by = models.CharField(max_length=255, blank=True)
    against = models.CharField(max_length=255, blank=True)
    severity = models.CharField(max_length=20, choices=NCSeverity.choices, default=NCSeverity.MINOR)
    status = models.CharField(max_length=20, choices=NCStatus.choices, default=NCStatus.OPEN, db_index=True)
    detail = models.TextField(blank=True)
    capa = models.JSONField(default=list, blank=True)

    raised_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "quality non-conformities"

    def __str__(self):
        return self.reference
