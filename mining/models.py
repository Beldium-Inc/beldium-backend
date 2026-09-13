"""Domain models for the Mining Compliance vertical.

A mining company (an ``Organisation`` of type ``mining_company``) operates one
or more ``MineSite`` records, each admitted and then continuously monitored
through the same ten-section review pattern the Processing vertical uses:
sections carry a reviewer verdict, evidence is reviewed piece by piece, and
findings/inspections/samples/environmental/safety data accumulate against the
site once it is live.

This mirrors ``processing.models`` closely by design — the two verticals model
the same kind of real-world compliance relationship for two different
industries — but is kept as its own app rather than folded into ``processing``
so a change to one industry's rules cannot silently move the other's.
"""
import secrets
from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel

UPLOAD_EXTENSIONS = ["pdf", "doc", "docx", "jpg", "jpeg", "png", "xlsx", "zip"]


def _reference(prefix, width=4):
    return f"{prefix}-{timezone.now().year}-{secrets.token_hex(width // 2).upper()}"


def generate_site_code():
    return _reference("BLM-SITE")


def generate_application_reference():
    return _reference("BLM-APP")


def generate_non_conformity_reference():
    return _reference("BLM-NC")


def generate_inspection_reference():
    return _reference("BLM-INS")


def generate_sample_reference():
    return _reference("BLM-SMP")


class SectionKey(models.TextChoices):
    CORPORATE = "corporate", "Corporate & Legal Identity"
    LICENCE = "licence", "Mining Licence & Permits"
    SITE = "site", "Site Infrastructure"
    OWNERSHIP = "ownership", "Ownership & Beneficial Interests"
    ENVIRONMENTAL = "environmental", "Environmental Management"
    SAFETY = "safety", "Health & Safety"
    EQUIPMENT = "equipment", "Equipment & Plant"
    PRODUCTION = "production", "Production Controls"
    SAMPLING = "sampling", "Sampling & Assay"
    INSPECTION = "inspection", "Physical Inspection Readiness"


class ReviewState(models.TextChoices):
    PENDING = "pending", "Pending review"
    UNDER_REVIEW = "under_review", "Under review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    INFO_REQUESTED = "info_requested", "Information requested"
    INSPECTION_REQUESTED = "inspection_requested", "Inspection requested"
    FLAGGED = "flagged", "Flagged"


class SiteStatus(models.TextChoices):
    OPERATIONAL = "operational", "Operational"
    UNDER_REVIEW = "under_review", "Under review"
    SUSPENDED = "suspended", "Suspended"
    CARE_MAINTENANCE = "care_maintenance", "Care & maintenance"


class RiskBand(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class MiningOrganisationProfile(TimeStampedModel):
    """The directors/beneficial-ownership detail a mining company's onboarding
    collects, kept apart from ``organisations.Organisation`` since it is
    specific to this vertical rather than to any organisation on the platform.
    """

    organisation = models.OneToOneField(
        "organisations.Organisation", on_delete=models.CASCADE, related_name="mining_profile"
    )
    directors = models.JSONField(default=list, blank=True)
    beneficial_owners = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"Mining profile: {self.organisation.name}"


class MineSite(TimeStampedModel):
    """A single mine site on the register."""

    code = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True, related_name="mine_sites"
    )
    name = models.CharField(max_length=255)
    mineral = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=100, blank=True, db_index=True)
    lga = models.CharField(max_length=100, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    area_ha = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=SiteStatus.choices, default=SiteStatus.UNDER_REVIEW, db_index=True)
    # 0-100, maintained by review/decision outcomes so the register shows the
    # score a decision was actually taken against, not one recomputed later.
    compliance_score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    risk = models.CharField(max_length=10, choices=RiskBand.choices, default=RiskBand.MEDIUM)
    capacity_tpa = models.PositiveBigIntegerField(default=0, help_text="Rated capacity, tonnes per annum.")
    current_tpa = models.PositiveBigIntegerField(default=0, help_text="Current throughput, tonnes per annum.")
    workforce = models.PositiveIntegerField(default=0)
    last_inspection_on = models.DateField(null=True, blank=True)
    # {"site": bool, "licence": bool, "documents": bool, "gps": bool}
    verification = models.JSONField(default=dict, blank=True)
    risk_reasons = models.JSONField(default=list, blank=True)
    production = models.JSONField(default=list, blank=True, help_text="[{month, tonnes, grade}]")
    inventory = models.JSONField(default=list, blank=True, help_text="[{item, qty, location, updated}]")
    transactions = models.JSONField(default=list, blank=True, help_text="[{date, buyer, tonnes, value, status}]")

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organisation", "name"], name="unique_mine_site_name_per_org")
        ]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = generate_site_code()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ScoreFactor(TimeStampedModel):
    """One weighted contributor to a site's compliance score."""

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="score_factors")
    label = models.CharField(max_length=200)
    weight = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(100)])
    score = models.PositiveSmallIntegerField(validators=[MaxValueValidator(100)])
    reason = models.TextField(blank=True)
    trend = models.CharField(
        max_length=10, choices=[("up", "Up"), ("down", "Down"), ("flat", "Flat")], default="flat"
    )

    class Meta:
        ordering = ["-weight", "created_at"]

    def __str__(self):
        return self.label


class ReviewSection(TimeStampedModel):
    """One of the ten evidence sections, plus the reviewer's verdict on it.

    ``fields`` is a list of ``{"label", "value", "flag", "note"}`` entries,
    held as submitted data rather than as columns because the prompts differ
    per mineral and evolve with the form.
    """

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="sections")
    key = models.CharField(max_length=20, choices=SectionKey.choices, db_index=True)
    title = models.CharField(max_length=200, blank=True)
    summary = models.TextField(blank=True)
    weight = models.PositiveSmallIntegerField(default=10)
    score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=25, choices=ReviewState.choices, default=ReviewState.PENDING, db_index=True)
    fields = models.JSONField(default=list, blank=True)
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_mining_sections"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["site", "key"], name="unique_mine_site_section")]

    def __str__(self):
        return f"{self.site_id}:{self.key}"


class Evidence(TimeStampedModel):
    """One piece of evidence attached to a review section."""

    class Kind(models.TextChoices):
        PDF = "pdf", "PDF"
        IMAGE = "image", "Image"
        CERTIFICATE = "certificate", "Certificate"
        SPREADSHEET = "spreadsheet", "Spreadsheet"
        REPORT = "report", "Report"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    section = models.ForeignKey(ReviewSection, on_delete=models.CASCADE, related_name="evidence")
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.PDF)
    file = models.FileField(
        upload_to="mining/evidence/%Y/%m/", blank=True, validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)]
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="uploaded_mining_evidence"
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""

    def __str__(self):
        return self.name


class LicenceDoc(TimeStampedModel):
    """A mining licence or permit held for a site."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRING = "expiring", "Expiring"
        EXPIRED = "expired", "Expired"
        SUSPENDED = "suspended", "Suspended"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="licences")
    number = models.CharField(max_length=100)
    type = models.CharField(max_length=150)
    authority = models.CharField(max_length=200, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    file = models.FileField(
        upload_to="mining/licences/%Y/%m/", blank=True, validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)]
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.type} · {self.number}"


class DocumentRecord(TimeStampedModel):
    """A general compliance document held against a site."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=150, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    file = models.FileField(
        upload_to="mining/documents/%Y/%m/", blank=True, validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)]
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="uploaded_mining_documents"
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""

    def __str__(self):
        return self.name


class NonConformity(TimeStampedModel):
    """A finding raised against a mine site."""

    class Severity(models.TextChoices):
        MINOR = "minor", "Minor"
        MAJOR = "major", "Major"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        AWAITING_REVIEW = "awaiting_review", "Awaiting review"
        CLOSED = "closed", "Closed"
        ESCALATED = "escalated", "Escalated"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="non_conformities")
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=150, blank=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    required_action = models.TextField(blank=True)
    responsible_person = models.CharField(max_length=200, blank=True)
    deadline = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="raised_mining_non_conformities"
    )

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_non_conformity_reference()
        return super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return self.status != self.Status.CLOSED and self.deadline < timezone.localdate()

    def __str__(self):
        return f"{self.reference} - {self.title}"


class CorrectiveSubmission(TimeStampedModel):
    """Corrective-action evidence submitted against a finding."""

    class Decision(models.TextChoices):
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        MORE_INFO = "more_info_requested", "More information requested"

    non_conformity = models.ForeignKey(NonConformity, on_delete=models.CASCADE, related_name="submissions")
    message = models.TextField(blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="submitted_mining_corrective_actions"
    )
    file = models.FileField(
        upload_to="mining/corrective-actions/%Y/%m/", blank=True, validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)]
    )
    decision = models.CharField(max_length=25, choices=Decision.choices, blank=True)
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_mining_corrective_actions"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Inspection(TimeStampedModel):
    class Type(models.TextChoices):
        PRE_APPROVAL = "pre_approval", "Pre-approval"
        ROUTINE = "routine", "Routine"
        FOLLOW_UP = "follow_up", "Follow-up"
        INCIDENT_TRIGGERED = "incident_triggered", "Incident-triggered"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        OVERDUE = "overdue", "Overdue"

    class Result(models.TextChoices):
        PASS = "pass", "Pass"
        PASS_WITH_OBSERVATIONS = "pass_with_observations", "Pass with observations"
        FAIL = "fail", "Fail"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="inspections")
    type = models.CharField(max_length=30, choices=Type.choices, default=Type.PRE_APPROVAL)
    scheduled_for = models.DateField(null=True, blank=True)
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="mining_inspections"
    )
    inspector_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    result = models.CharField(max_length=30, choices=Result.choices, blank=True)
    findings = models.JSONField(default=list, blank=True, help_text="[{area, observation, severity}]")
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-scheduled_for", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_inspection_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class Sample(TimeStampedModel):
    class Status(models.TextChoices):
        VERIFIED = "verified", "Verified"
        PENDING = "pending", "Pending"
        DISPUTED = "disputed", "Disputed"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="samples")
    collected_on = models.DateField(default=timezone.localdate)
    lab = models.CharField(max_length=200, blank=True)
    certificate = models.CharField(max_length=150, blank=True)
    li2o_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fe2o3_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    moisture_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    method = models.CharField(max_length=150, blank=True)
    chain_of_custody = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-collected_on"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_sample_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class EnvRecord(TimeStampedModel):
    class Status(models.TextChoices):
        WITHIN_LIMIT = "within_limit", "Within limit"
        WATCH = "watch", "Watch"
        BREACH = "breach", "Breach"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="env_records")
    metric = models.CharField(max_length=150)
    value = models.CharField(max_length=100)
    limit = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.WITHIN_LIMIT, db_index=True)
    measured_on = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["-measured_on"]

    def __str__(self):
        return f"{self.metric} · {self.site_id}"


class SafetyIncident(TimeStampedModel):
    class Severity(models.TextChoices):
        MINOR = "minor", "Minor"
        MAJOR = "major", "Major"
        CRITICAL = "critical", "Critical"

    class Status(models.TextChoices):
        INVESTIGATING = "investigating", "Investigating"
        CLOSED = "closed", "Closed"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="safety_incidents")
    date = models.DateField(default=timezone.localdate)
    type = models.CharField(max_length=150)
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    lost_days = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVESTIGATING, db_index=True)
    summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.type} · {self.site_id}"


class Equipment(TimeStampedModel):
    class Status(models.TextChoices):
        CERTIFIED = "certified", "Certified"
        DUE_INSPECTION = "due_inspection", "Due inspection"
        OUT_OF_SERVICE = "out_of_service", "Out of service"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="equipment")
    name = models.CharField(max_length=200)
    serial = models.CharField(max_length=150, blank=True)
    cert_expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CERTIFIED, db_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProductionRecord(TimeStampedModel):
    """One reported period of production output at a mine site."""

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="production_records")
    period_start = models.DateField()
    period_end = models.DateField()
    commodity = models.CharField(max_length=120)
    tonnage = models.DecimalField(max_digits=12, decimal_places=2)
    grade = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_start"]

    def __str__(self):
        return f"{self.commodity} · {self.site_id} ({self.period_start})"


class InventoryItem(TimeStampedModel):
    """One stockpile, consumable or spare held at a mine site."""

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="inventory_items")
    category = models.CharField(max_length=60)  # stockpile / consumable / spare
    name = models.CharField(max_length=150)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=30)
    threshold = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} · {self.site_id}"


class Application(TimeStampedModel):
    """An admission or amendment application, referencing a not-yet-live site."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        INFO_REQUESTED = "info_requested", "Information requested"

    reference = models.CharField(max_length=32, unique=True, editable=False, blank=True)
    organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.SET_NULL, null=True, blank=True, related_name="mining_applications"
    )
    site = models.ForeignKey(
        MineSite, on_delete=models.SET_NULL, null=True, blank=True, related_name="applications"
    )
    site_name = models.CharField(max_length=255, blank=True)
    type = models.CharField(max_length=150, blank=True)
    mineral = models.CharField(max_length=100, blank=True)
    submitted_on = models.DateField(null=True, blank=True)
    stage = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_mining_applications"
    )
    sla_days = models.PositiveSmallIntegerField(default=30)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_mining_applications"
    )

    class Meta:
        ordering = ["-submitted_on", "-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = generate_application_reference()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.reference} - {self.site_name}"


class PendingReview(TimeStampedModel):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="pending_reviews")
    subject = models.CharField(max_length=255)
    type = models.CharField(max_length=150, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    submitted_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_mining_reviews"
    )

    class Meta:
        ordering = ["due_on", "-created_at"]


class InfoRequest(TimeStampedModel):
    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESPONDED = "responded", "Responded"
        CLOSED = "closed", "Closed"

    site = models.ForeignKey(MineSite, on_delete=models.CASCADE, related_name="info_requests")
    section = models.CharField(max_length=20, blank=True, help_text="A SectionKey, or blank for 'general'.")
    subject = models.CharField(max_length=255)
    details = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="raised_mining_info_requests"
    )
    due_by = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    response_message = models.TextField(blank=True)
    response_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="responded_mining_info_requests"
    )
    response_at = models.DateTimeField(null=True, blank=True)
    response_attachments = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]
