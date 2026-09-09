"""Domain models for the Processing Compliance vertical.

A processor is a company that transforms mineral feedstock: crushing, chemical
refining, smelting or sorting. It is registered once (``Processor``), operates
one or more ``Facility`` records, and is admitted to the register through a
``ProcessingApplication`` that carries ten evidence sections. After admission
the register keeps accumulating operational evidence against it — inspections,
non-conformities, environmental alerts, incidents and traceability runs.

The organisations app already owns identity and membership, so a processor
points at an ``Organisation`` rather than restating who its people are.
"""
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


# A document inside this window is neither comfortably valid nor yet expired;
# the register surfaces it so the holder can renew before it lapses.
EXPIRY_WARNING_DAYS = 60


def _reference(prefix, width=4):
    return f"{prefix}-{timezone.now().year}-{secrets.token_hex(width // 2).upper()}"


def generate_processor_reference():
    return _reference("BLD-PRC")


def generate_application_reference():
    return _reference("BPC-APP")


def generate_non_conformity_reference():
    return _reference("BPC-NC")


def generate_inspection_reference():
    return _reference("BPC-INS")


def generate_alert_reference():
    return _reference("BPC-ENV")


def generate_incident_reference():
    return _reference("BPC-INC")


def generate_run_reference():
    return _reference("BPC-RUN")


class ProcessingType(models.TextChoices):
    CRUSHING_MILLING = "crushing_milling", "Crushing & Milling"
    CHEMICAL_REFINING = "chemical_refining", "Chemical Processing / Refining"
    SMELTING = "smelting", "Smelting & Thermal Recovery"
    SORTING_BALING = "sorting_baling", "Sorting, Washing & Baling"


class SectionKey(models.TextChoices):
    CORPORATE = "corporate", "Corporate & Legal Identity"
    REGULATORY = "regulatory", "Regulatory Licences & Permits"
    FACILITY = "facility", "Facility & Site Infrastructure"
    ENVIRONMENTAL = "environmental", "Environmental Management"
    HEALTH_SAFETY = "health_safety", "Health, Safety & Workforce"
    EQUIPMENT = "equipment", "Equipment & Process Technology"
    OPERATIONAL = "operational", "Operational Controls"
    QUALITY = "quality", "Quality Controls & Laboratory"
    WASTE = "waste", "Waste & Residue Handling"
    INSPECTION = "inspection", "Physical Inspection Readiness"


class ReviewState(models.TextChoices):
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    INFO_REQUESTED = "info_requested", "Information requested"
    FLAGGED = "flagged", "Flagged"


class ApplicationStage(models.TextChoices):
    NEW = "new", "New"
    IN_REVIEW = "in_review", "In review"
    AWAITING_INFO = "awaiting_info", "Awaiting information"
    INSPECTION = "inspection", "Inspection"
    DECIDED = "decided", "Decided"


class ApplicationDecision(models.TextChoices):
    APPROVED = "approved", "Approved"
    CONDITIONAL = "conditional_approval", "Conditional approval"
    MORE_INFO = "more_info_required", "More information required"
    REJECTED = "rejected", "Rejected"


class ProcessorStatus(models.TextChoices):
    UNDER_REVIEW = "under_review", "Under review"
    APPROVED = "approved", "Approved"
    CONDITIONAL = "conditional", "Conditional"
    SUSPENDED = "suspended", "Suspended"


UPLOAD_EXTENSIONS = ["pdf", "doc", "docx", "jpg", "jpeg", "png"]


class Processor(TimeStampedModel):
    """A processing company on the register."""

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processors",
    )
    name = models.CharField(max_length=255)
    rc_number = models.CharField(max_length=100, blank=True, db_index=True)
    tin = models.CharField(max_length=100, blank=True)
    processing_type = models.CharField(max_length=30, choices=ProcessingType.choices, db_index=True)
    country = models.CharField(max_length=100, default="Nigeria")
    state = models.CharField(max_length=100, blank=True, db_index=True)
    lga = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=50, blank=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=ProcessorStatus.choices, default=ProcessorStatus.UNDER_REVIEW, db_index=True
    )
    # 0-100. Maintained by review outcomes rather than derived on read, so the
    # register can show the score a decision was actually taken against.
    compliance_score = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)]
    )
    registered_on = models.DateField(default=timezone.localdate)
    last_inspection_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_processor_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Facility(TimeStampedModel):
    """One physical plant belonging to a processor."""

    processor = models.ForeignKey(Processor, on_delete=models.CASCADE, related_name="facilities")
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    state = models.CharField(max_length=100, blank=True, db_index=True)
    lga = models.CharField(max_length=100, blank=True)
    capacity = models.CharField(max_length=100, blank=True, help_text="Rated throughput, e.g. '180 t/month'.")
    workforce = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["processor", "name"], name="unique_facility_name_per_processor")
        ]

    def __str__(self):
        return self.name


class ProcessingApplication(TimeStampedModel):
    """A processor's admission application, reviewed section by section."""

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    processor = models.ForeignKey(
        Processor, on_delete=models.SET_NULL, null=True, blank=True, related_name="applications"
    )
    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_applications",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_processing_applications",
    )

    company = models.CharField(max_length=255)
    rc_number = models.CharField(max_length=100, blank=True)
    tin = models.CharField(max_length=100, blank=True)
    processing_type = models.CharField(max_length=30, choices=ProcessingType.choices, db_index=True)
    state = models.CharField(max_length=100, blank=True, db_index=True)
    lga = models.CharField(max_length=100, blank=True)
    facility_name = models.CharField(max_length=255, blank=True)
    capacity = models.CharField(max_length=100, blank=True)
    workforce = models.PositiveIntegerField(default=0)

    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)

    stage = models.CharField(
        max_length=20, choices=ApplicationStage.choices, default=ApplicationStage.NEW, db_index=True
    )
    decision = models.CharField(max_length=30, choices=ApplicationDecision.choices, blank=True)
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_processing_applications",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    submitted_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_on", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_application_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.company}"


class ApplicationSection(TimeStampedModel):
    """One of the ten evidence sections, plus the reviewer's verdict on it.

    ``fields`` is a list of ``{"label", "value", "flag"}`` entries. The set of
    labels differs per processing type and evolves with the form, so it is held
    as submitted data rather than as columns.
    """

    application = models.ForeignKey(ProcessingApplication, on_delete=models.CASCADE, related_name="sections")
    key = models.CharField(max_length=30, choices=SectionKey.choices, db_index=True)
    fields = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    review_state = models.CharField(
        max_length=20, choices=ReviewState.choices, default=ReviewState.PENDING, db_index=True
    )
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_processing_sections",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(fields=["application", "key"], name="unique_application_section")
        ]

    def __str__(self):
        return f"{self.application_id}:{self.key}"


class RiskCause(TimeStampedModel):
    """One weighted contributor to an application's risk score.

    The score is the sum of its causes, so a reviewer can always see what a
    number is made of instead of being handed an opaque total.
    """

    application = models.ForeignKey(ProcessingApplication, on_delete=models.CASCADE, related_name="risk_causes")
    cause = models.CharField(max_length=200)
    weight = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(100)])
    detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-weight", "created_at"]

    def __str__(self):
        return self.cause


class ProcessingDocument(TimeStampedModel):
    """Evidence attached to an application section or held against a processor.

    Validity is derived from ``expires_on`` rather than stored: a stored status
    silently goes stale, and every reader here cares about the date anyway.
    """

    application = models.ForeignKey(
        ProcessingApplication, on_delete=models.CASCADE, null=True, blank=True, related_name="documents"
    )
    processor = models.ForeignKey(
        Processor, on_delete=models.CASCADE, null=True, blank=True, related_name="documents"
    )
    section = models.CharField(max_length=30, choices=SectionKey.choices, db_index=True)
    name = models.CharField(max_length=255)
    reference = models.CharField(max_length=150, blank=True)
    issuer = models.CharField(max_length=200, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True, db_index=True)
    file = models.FileField(
        upload_to="processing/documents/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)],
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_processing_documents",
    )
    # The desk accepts or rejects each piece of evidence individually; the
    # section verdict above it is a separate, coarser judgement.
    review_state = models.CharField(
        max_length=20, choices=ReviewState.choices, default=ReviewState.PENDING, db_index=True
    )
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_processing_documents",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["section", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(application__isnull=False) | models.Q(processor__isnull=False),
                name="processing_document_has_owner",
            )
        ]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""

    @property
    def status(self):
        """``missing`` | ``expired`` | ``expiring`` | ``valid``."""
        if not self.file:
            return "missing"
        if not self.expires_on:
            return "valid"
        today = timezone.localdate()
        if self.expires_on < today:
            return "expired"
        if self.expires_on <= today + timedelta(days=EXPIRY_WARNING_DAYS):
            return "expiring"
        return "valid"

    @property
    def days_to_expiry(self):
        return None if not self.expires_on else (self.expires_on - timezone.localdate()).days

    def __str__(self):
        return self.name


class NonConformity(TimeStampedModel):
    """A finding raised against an application section or a live processor."""

    class Severity(models.TextChoices):
        MINOR = "minor", "Minor"
        MAJOR = "major", "Major"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        EVIDENCE_SUBMITTED = "evidence_submitted", "Evidence submitted"
        CLOSED = "closed", "Closed"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    application = models.ForeignKey(
        ProcessingApplication, on_delete=models.CASCADE, null=True, blank=True, related_name="non_conformities"
    )
    processor = models.ForeignKey(
        Processor, on_delete=models.CASCADE, null=True, blank=True, related_name="non_conformities"
    )
    section = models.CharField(max_length=30, choices=SectionKey.choices, db_index=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    title = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    raised_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField()
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN, db_index=True)
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="raised_non_conformities",
    )
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_non_conformities",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-raised_on", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_non_conformity_reference()
        return super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return self.status != self.Status.CLOSED and self.due_on < timezone.localdate()

    def __str__(self):
        return f"{self.reference} - {self.title}"


class NonConformityEvidence(TimeStampedModel):
    """Corrective-action evidence submitted against a finding."""

    non_conformity = models.ForeignKey(NonConformity, on_delete=models.CASCADE, related_name="evidence")
    name = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    file = models.FileField(
        upload_to="processing/non-conformities/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)],
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_processing_evidence",
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""

    def __str__(self):
        return self.name


class Inspection(TimeStampedModel):
    """A site visit, either pre-approval or against a registered processor."""

    class Type(models.TextChoices):
        PRE_APPROVAL = "pre_approval", "Pre-approval"
        ROUTINE = "routine", "Routine"
        FOLLOW_UP = "follow_up", "Follow-up"
        INCIDENT_TRIGGERED = "incident_triggered", "Incident-triggered"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    application = models.ForeignKey(
        ProcessingApplication, on_delete=models.SET_NULL, null=True, blank=True, related_name="inspections"
    )
    processor = models.ForeignKey(
        Processor, on_delete=models.CASCADE, null=True, blank=True, related_name="inspections"
    )
    facility = models.ForeignKey(
        Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name="inspections"
    )
    facility_name = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=100, blank=True)
    # Null until a date is agreed; the register reads that as "to be confirmed".
    scheduled_for = models.DateField(null=True, blank=True, db_index=True)
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_inspections",
    )
    inspector_name = models.CharField(max_length=200, blank=True)
    inspection_type = models.CharField(max_length=30, choices=Type.choices, default=Type.PRE_APPROVAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    outcome = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_processing_inspections",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-scheduled_for", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_inspection_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class EnvironmentalAlert(TimeStampedModel):
    """A monitored parameter that has crossed its permitted threshold."""

    class Severity(models.TextChoices):
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    processor = models.ForeignKey(
        Processor, on_delete=models.CASCADE, null=True, blank=True, related_name="environmental_alerts"
    )
    facility = models.ForeignKey(
        Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name="environmental_alerts"
    )
    facility_name = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=100, blank=True, db_index=True)
    parameter = models.CharField(max_length=150)
    reading = models.CharField(max_length=100)
    threshold = models.CharField(max_length=100)
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    detected_on = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_environmental_alerts",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-detected_on", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_alert_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.parameter}"


class Incident(TimeStampedModel):
    """A reportable event at a facility: emission, spill, failure or injury."""

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MODERATE = "moderate", "Moderate"
        SEVERE = "severe", "Severe"

    class Status(models.TextChoices):
        REPORTED = "reported", "Reported"
        UNDER_INVESTIGATION = "under_investigation", "Under investigation"
        CLOSED = "closed", "Closed"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    processor = models.ForeignKey(
        Processor, on_delete=models.CASCADE, null=True, blank=True, related_name="incidents"
    )
    facility = models.ForeignKey(
        Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name="incidents"
    )
    facility_name = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=100, blank=True, db_index=True)
    incident_type = models.CharField(max_length=150)
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    reported_on = models.DateField(default=timezone.localdate, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.REPORTED, db_index=True)
    summary = models.TextField(blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_processing_incidents",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closure_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-reported_on", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_incident_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.incident_type}"


class TraceabilityRun(TimeStampedModel):
    """One production run: an input batch transformed into an output batch.

    Masses are kilograms so the yield is exact; the API converts to tonnes for
    display. Storing tonnes as a float would make reconciliation lossy.
    """

    class Verdict(models.TextChoices):
        PASS = "pass", "Pass"
        HOLD = "hold", "Hold"
        FAIL = "fail", "Fail"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    processor = models.ForeignKey(Processor, on_delete=models.CASCADE, related_name="runs")
    facility = models.ForeignKey(
        Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name="runs"
    )
    facility_name = models.CharField(max_length=255, blank=True)

    input_batch = models.CharField(max_length=100, db_index=True)
    input_source = models.CharField(max_length=255, blank=True)
    input_mass_kg = models.PositiveBigIntegerField()
    process = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    output_batch = models.CharField(max_length=100, db_index=True)
    output_mass_kg = models.PositiveBigIntegerField(default=0)

    qc_assay = models.CharField(max_length=150, blank=True)
    qc_moisture = models.CharField(max_length=50, blank=True)
    qc_verdict = models.CharField(max_length=20, choices=Verdict.choices, default=Verdict.PASS)
    qc_lab = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_run_reference()
        return super().save(*args, **kwargs)

    @property
    def yield_percent(self):
        if not self.input_mass_kg:
            return 0.0
        return round(self.output_mass_kg / self.input_mass_kg * 100, 1)

    def __str__(self):
        return self.reference


class ComplianceReport(TimeStampedModel):
    """A generated periodic report held for download by the oversight desk."""

    reference = models.CharField(max_length=40, unique=True)
    title = models.CharField(max_length=255)
    period_label = models.CharField(max_length=100, blank=True)
    scope = models.CharField(max_length=100, blank=True)
    generated_on = models.DateField(default=timezone.localdate)
    pages = models.PositiveSmallIntegerField(default=0)
    file = models.FileField(
        upload_to="processing/reports/%Y/%m/", blank=True, validators=[FileExtensionValidator(["pdf"])]
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_processing_reports",
    )

    class Meta:
        ordering = ["-generated_on"]

    def __str__(self):
        return self.title
