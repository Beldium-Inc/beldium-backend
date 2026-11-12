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
    # Rows carried over from the old system are read only and marked legacy.
    # Set by the 0005 data migration; new samples are never legacy.
    is_legacy = models.BooleanField(default=False, db_index=True)

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
    # A certificate cannot disappear with its sample. Corrections are new records.
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, related_name="certificates")
    issued_at = models.DateTimeField(default=timezone.now)
    issued_by = models.CharField(max_length=255, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=CertificateStatus.choices, default=CertificateStatus.ACTIVE, db_index=True)
    verification_hash = models.CharField(max_length=128, blank=True)
    scans = models.PositiveIntegerField(default=0)
    is_legacy = models.BooleanField(default=False, db_index=True)
    # The rule version the issuing decision used (G5). Blank on rows issued
    # before settings existed.
    rule_version = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Blank hashes are allowed (old rows never had one). Every real hash is unique.
            models.UniqueConstraint(
                fields=["verification_hash"],
                condition=~models.Q(verification_hash=""),
                name="unique_certificate_verification_hash",
            ),
        ]

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
    is_legacy = models.BooleanField(default=False, db_index=True)

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


# --- Q&C roles (Release 1, task G3) ------------------------------------------
#
# The 12 roles and the access grid from Figure 5 are stored as rows, seeded by
# a data migration from quality/roles.py. A role assignment gives no access
# until Q&C approves it, and it is never deleted: ending it is a new state.


class QualityRole(models.Model):
    code = models.CharField(max_length=8, primary_key=True)
    name = models.CharField(max_length=80)
    is_internal = models.BooleanField(default=False)
    second_factor_required = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class QualityRoleGrant(models.Model):
    """One cell of the access grid: what a role may do on a record type."""

    role = models.ForeignKey(QualityRole, on_delete=models.PROTECT, related_name="grants")
    record_type = models.CharField(max_length=40)
    letters = models.CharField(max_length=4, help_text="C create, R read, U update, A approve")
    restricted = models.BooleanField(default=False)

    class Meta:
        ordering = ["role", "record_type"]
        constraints = [
            models.UniqueConstraint(fields=["role", "record_type"], name="unique_quality_role_grant"),
        ]


class RoleAssignmentStatus(models.TextChoices):
    PENDING_APPROVAL = "pending_approval", "Pending approval"
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"
    REJECTED = "rejected", "Rejected"


class QualityRoleAssignment(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="quality_role_assignments")
    role = models.ForeignKey(QualityRole, on_delete=models.PROTECT, related_name="assignments")
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, null=True, blank=True,
        related_name="quality_role_assignments",
    )
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=RoleAssignmentStatus.choices,
        default=RoleAssignmentStatus.PENDING_APPROVAL, db_index=True,
    )
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def delete(self, *args, **kwargs):
        raise PermissionError("Role assignments are never deleted. End the assignment instead.")

    def __str__(self):
        return f"{self.user_id} {self.role_id} {self.status}"


# --- audit log (Release 1, task G4) -----------------------------------------


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit entries are append only.")

    def delete(self):
        raise PermissionError("Audit entries are append only.")


class AuditOutcome(models.TextChoices):
    SUCCESS = "success", "Success"
    REFUSED = "refused", "Refused"


class QualityAuditEntry(models.Model):
    """One append only row per action on a Q&C record, refused ones included.

    The actor is stored as an id and a text copy, not a foreign key, so a
    removed user account never changes or blocks the history. Updates and
    deletes are refused here, in the admin, and by a database trigger
    (migration 0009).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor_id = models.UUIDField(null=True, blank=True, db_index=True)
    actor_name = models.CharField(max_length=255, blank=True)
    actor_email = models.CharField(max_length=254, blank=True)
    actor_role_code = models.CharField(max_length=80, blank=True)
    action = models.CharField(max_length=120, db_index=True)
    record_type = models.CharField(max_length=60, blank=True, db_index=True)
    record_id = models.CharField(max_length=64, blank=True, db_index=True)
    record_label = models.CharField(max_length=120, blank=True)
    batch_id = models.CharField(max_length=64, blank=True)
    outcome = models.CharField(max_length=10, choices=AuditOutcome.choices, default=AuditOutcome.SUCCESS, db_index=True)
    reason = models.TextField(blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=255, blank=True)
    rule_version = models.CharField(max_length=40, blank=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-occurred_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit entries are append only.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit entries are append only.")


# --- settings, change log and rule versions (Release 1, task G5) -------------


class SettingValueType(models.TextChoices):
    POSITIVE_INT = "positive_int", "Whole number above zero"
    POSITIVE_INT_LIST = "positive_int_list", "List of whole numbers above zero"
    BOOLEAN = "boolean", "Yes or no"
    TEXT = "text", "Text"
    TEXT_LIST = "text_list", "List of text"
    JSON = "json", "Structured value"


class QualitySetting(models.Model):
    """A named Q&C rule value. The value itself lives in the change log, so
    every value it ever had, and when it took effect, is kept."""

    key = models.CharField(max_length=60, primary_key=True)
    label = models.CharField(max_length=200)
    unit = models.CharField(max_length=30, blank=True)
    value_type = models.CharField(max_length=20, choices=SettingValueType.choices)
    owner = models.CharField(max_length=120, help_text="Who decides the value")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["key"]

    def delete(self, *args, **kwargs):
        raise PermissionError("Settings are never deleted.")


class QualityRuleVersion(models.Model):
    """Each settings change that takes effect starts a new rule version.
    Decisions store the version they were made under."""

    number = models.PositiveIntegerField(primary_key=True)
    effective_from = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-number"]

    def __str__(self):
        return str(self.number)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Rule versions are append only.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Rule versions are append only.")


class QualitySettingChange(models.Model):
    """Append only. A setting changes only by a new row here. A null value
    means the value is a placeholder that Q&C has not given yet."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    setting = models.ForeignKey(QualitySetting, on_delete=models.PROTECT, related_name="changes")
    value = models.JSONField(null=True, blank=True)
    effective_from = models.DateTimeField(db_index=True)
    reason = models.TextField()
    changed_by_id = models.UUIDField(null=True, blank=True)
    changed_by_name = models.CharField(max_length=255, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)
    rule_version = models.ForeignKey(QualityRuleVersion, on_delete=models.PROTECT, related_name="changes")

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from", "-changed_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Setting changes are append only.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Setting changes are append only.")


# --- ID counters (Release 1, task G6) ----------------------------------------


class QualityIdCounter(models.Model):
    """One locked counter row per prefix and year. Numbers are never reused."""

    prefix = models.CharField(max_length=40)
    year = models.PositiveSmallIntegerField()
    last_value = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["prefix", "year"], name="unique_quality_id_counter"),
        ]

    def delete(self, *args, **kwargs):
        raise PermissionError("ID counters are never deleted, so numbers are never reused.")
