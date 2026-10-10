import secrets
import uuid
from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


UPLOAD_EXTENSIONS = ["pdf", "doc", "docx", "jpg", "jpeg", "png", "xls", "xlsx", "zip"]


def application_reference():
    return f"BLD-QA-APP-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def professional_reference():
    return f"BLD-QA-PRO-{timezone.now().year}-{secrets.token_hex(4).upper()}"


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
    # Set when this record mirrors an organisation's onboarding application
    # (see quality/bridge.py); decisions taken here are carried back to it.
    compliance_application = models.OneToOneField(
        "compliance.ComplianceApplication", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quality_application",
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
    miner_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    partner_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    buyer_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
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


class QualityApplicationDocument(TimeStampedModel):
    application = models.ForeignKey(QualityApplication, on_delete=models.CASCADE, related_name="uploaded_documents")
    document_id = models.CharField(max_length=80)
    name = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=150, blank=True)
    file = models.FileField(
        upload_to="quality/application-documents/%Y/%m/",
        validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)],
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["application", "document_id"], name="unique_quality_application_document")
        ]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""


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


class QualityNotification(TimeStampedModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quality_notifications")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    event = models.CharField(max_length=80, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    sample = models.ForeignKey(Sample, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    application = models.ForeignKey(QualityApplication, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    certificate = models.ForeignKey(Certificate, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    non_conformity = models.ForeignKey(QualityNonConformity, on_delete=models.CASCADE, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]


class QualityProfessionalApplication(TimeStampedModel):
    """An individual (officer, inspector) applying to work under a Q&C organisation.

    Beldium reviews the person here; whether the organisation takes them on is
    its own administrator's call, through the join request sent alongside.
    """

    applicant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quality_professional_applications")
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quality_professional_applications",
    )
    reference = models.CharField(max_length=50, unique=True, default=professional_reference, editable=False)
    role = models.CharField(max_length=40, default="officer_inspector")
    status = models.CharField(max_length=20, choices=ApplicationStatus.choices, default=ApplicationStatus.SUBMITTED, db_index=True)

    personal = models.JSONField(default=dict, blank=True)
    qualifications = models.JSONField(default=list, blank=True)
    certifications = models.JSONField(default=list, blank=True)
    capability = models.JSONField(default=dict, blank=True)
    experience = models.JSONField(default=dict, blank=True)
    declaration = models.JSONField(default=dict, blank=True)
    audit = models.JSONField(default=list, blank=True)
    decision_note = models.TextField(blank=True)

    submitted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference


class QualityProfessionalDocument(TimeStampedModel):
    application = models.ForeignKey(QualityProfessionalApplication, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=80)
    title = models.CharField(max_length=255)
    file = models.FileField(
        upload_to="quality/professional-documents/%Y/%m/",
        validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)],
    )

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["application", "document_type"], name="unique_quality_professional_document")
        ]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""
