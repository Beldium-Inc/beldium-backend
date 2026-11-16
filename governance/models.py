"""Governance, Risk and Policy Intelligence.

Unlike the seven compliance sectors, governance is not a register of outside
companies. It is Beldium's own oversight workspace: the policies it adopts,
the enterprise risks it carries, the regulatory requirements it tracks, the
stakeholders it engages, and read only intelligence drawn from every sector.

Access is by named membership, not by organisation: each person holds one
governance role, granted by invitation or by an approved access request. The
role decides what they may do (see governance/permissions.py).

Policy review events are append only. A policy is never edited in place once
it leaves draft: a revision request reopens it, and every transition writes a
PolicyEvent row that records who moved it, from what, to what, and why.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def _reference(prefix):
    return f"{prefix}-{timezone.now().year}-{secrets.token_hex(3).upper()}"


def policy_reference():
    return _reference("POL")


def risk_reference():
    return _reference("RSK")


def finding_reference():
    return _reference("REG")


def assessment_reference():
    return _reference("RIA")


def invitation_expiry():
    return timezone.now() + timedelta(days=14)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Sector(models.TextChoices):
    """The sectors governance records can be tagged against."""

    ALL = "all", "All sectors"
    MINING = "mining", "Mining"
    QUALITY = "quality", "Quality & Control"
    LOGISTICS = "logistics", "Logistics"
    WAREHOUSING = "warehousing", "Warehousing"
    PROCESSING = "processing", "Processing"
    EXPORT = "export", "Exports"
    MARKETPLACE = "marketplace", "Marketplace"


class GovernanceRole(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Administrator"
    EXECUTIVE = "executive", "Governance and Risk Executive"
    ANALYST = "analyst", "Governance Analyst"
    RISK_OFFICER = "risk_officer", "Risk and Policy Officer"
    REVIEWER = "reviewer", "Institutional Reviewer"
    VIEWER = "viewer", "Oversight Viewer"


class MemberStatus(models.TextChoices):
    PENDING = "pending", "Pending approval"
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    DECLINED = "declined", "Declined"


class GovernanceMember(TimeStampedModel):
    """One person's standing in the governance workspace."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="governance_member")
    role = models.CharField(max_length=20, choices=GovernanceRole.choices, default=GovernanceRole.VIEWER)
    status = models.CharField(max_length=20, choices=MemberStatus.choices, default=MemberStatus.PENDING, db_index=True)
    organisation = models.CharField(max_length=255, blank=True)
    job_title = models.CharField(max_length=200, blank=True)
    # The access request as submitted: professional identity, organisation,
    # qualifications and declarations. Kept as submitted for the record.
    profile = models.JSONField(default=dict, blank=True)
    scope = models.JSONField(default=list, blank=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["user__first_name", "user__last_name", "user__email"]

    def __str__(self):
        return f"{self.user.email} ({self.role})"


class InvitationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"


class GovernanceInvitation(TimeStampedModel):
    """An invitation to join the workspace in a named role.

    Only a hash of the token is stored. The plain token goes out in the email
    (and back to the inviter once, so a link can be copied by hand).
    """

    email = models.EmailField(db_index=True)
    name = models.CharField(max_length=200)
    organisation = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=GovernanceRole.choices)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=InvitationStatus.choices, default=InvitationStatus.PENDING, db_index=True)
    expires_at = models.DateTimeField(default=invitation_expiry)
    sent_count = models.PositiveSmallIntegerField(default=1)
    last_sent_at = models.DateTimeField(default=timezone.now)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()


class PolicyStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted for Review"
    UNDER_REVIEW = "under_review", "Under Review"
    REVISION_REQUESTED = "revision_requested", "Revision Requested"
    RECOMMENDED = "recommended", "Recommended"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published"
    REJECTED = "rejected", "Rejected"
    ARCHIVED = "archived", "Archived"


class Policy(TimeStampedModel):
    reference = models.CharField(max_length=32, unique=True, default=policy_reference, editable=False)
    title = models.CharField(max_length=255)
    domain = models.CharField(max_length=120)
    sector = models.CharField(max_length=20, choices=Sector.choices, default=Sector.ALL)
    responsible_organisation = models.CharField(max_length=255, blank=True)
    jurisdiction = models.CharField(max_length=120, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="governance_policies")
    review_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=PolicyStatus.choices, default=PolicyStatus.DRAFT, db_index=True)
    # Minor counts submissions since the last publication; major counts
    # publications. Shown as v{major}.{minor}.
    version_major = models.PositiveSmallIntegerField(default=0)
    version_minor = models.PositiveSmallIntegerField(default=1)
    executive_summary = models.TextField(blank=True)
    strategic_context = models.TextField(blank=True)
    objectives = models.TextField(blank=True)
    stakeholders = models.TextField(blank=True)
    regulatory_instruments = models.TextField(blank=True)
    expected_outcomes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name_plural = "policies"

    @property
    def version(self):
        return f"v{self.version_major}.{self.version_minor}"

    def __str__(self):
        return f"{self.reference} {self.title}"


class PolicyEvent(TimeStampedModel):
    """Append only record of every policy workflow step."""

    policy = models.ForeignKey(Policy, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=30)
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    version = models.CharField(max_length=20)
    comment = models.TextField(blank=True)
    # The policy text as it stood at this step, so a reviewer's decision can
    # always be read against the version it was taken on.
    snapshot = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]


class ControlEffectiveness(models.TextChoices):
    EFFECTIVE = "effective", "Effective"
    PARTIAL = "partial", "Partial"
    WEAK = "weak", "Weak"
    NONE = "none", "No control"


class RiskStatus(models.TextChoices):
    OPEN = "open", "Open"
    MONITORING = "monitoring", "Monitoring"
    ESCALATED = "escalated", "Escalated"
    CLOSED = "closed", "Closed"


class RiskCategory(models.TextChoices):
    GOVERNANCE = "governance", "Governance"
    FRAUD = "fraud", "Fraud"
    CYBERSECURITY = "cybersecurity", "Cybersecurity"
    ENVIRONMENTAL = "environmental", "Environmental"
    COUNTERPARTY = "counterparty", "Counterparty"
    OPERATIONAL = "operational", "Operational"
    REGULATORY = "regulatory", "Regulatory"
    REPUTATIONAL = "reputational", "Reputational"
    FINANCIAL = "financial", "Financial"


SCORE_1_5 = [MinValueValidator(1), MaxValueValidator(5)]


class Risk(TimeStampedModel):
    """An enterprise risk.

    Inherent score is likelihood times impact before controls. Residual
    likelihood and impact are assessed separately by the risk owner after
    controls; they are not derived from a formula, because how much a given
    control reduces a given risk is a judgement the register records rather
    than one it invents.
    """

    reference = models.CharField(max_length=32, unique=True, default=risk_reference, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=RiskCategory.choices)
    sector = models.CharField(max_length=20, choices=Sector.choices, default=Sector.ALL)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="governance_risks")
    likelihood = models.PositiveSmallIntegerField(validators=SCORE_1_5)
    impact = models.PositiveSmallIntegerField(validators=SCORE_1_5)
    controls = models.TextField(blank=True)
    control_effectiveness = models.CharField(max_length=20, choices=ControlEffectiveness.choices, default=ControlEffectiveness.NONE)
    residual_likelihood = models.PositiveSmallIntegerField(validators=SCORE_1_5)
    residual_impact = models.PositiveSmallIntegerField(validators=SCORE_1_5)
    treatment_plan = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=RiskStatus.choices, default=RiskStatus.OPEN, db_index=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-updated_at"]

    @property
    def inherent_score(self):
        return self.likelihood * self.impact

    @property
    def residual_score(self):
        return self.residual_likelihood * self.residual_impact


class FindingStatus(models.TextChoices):
    PENDING = "pending_verification", "Pending verification"
    VERIFIED = "verified", "Verified"
    ESCALATED = "escalated", "Escalated"
    CLOSED = "closed", "Closed"


class RegulatoryFinding(TimeStampedModel):
    """A regulatory requirement and the gap found against it."""

    reference = models.CharField(max_length=32, unique=True, default=finding_reference, editable=False)
    requirement = models.CharField(max_length=255)
    instrument = models.CharField(max_length=255, blank=True)
    authority = models.CharField(max_length=200)
    sector = models.CharField(max_length=20, choices=Sector.choices, default=Sector.ALL)
    gap = models.TextField(blank=True)
    source = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=30, choices=FindingStatus.choices, default=FindingStatus.PENDING, db_index=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class StakeholderCategory(models.TextChoices):
    REGULATOR = "regulator", "Regulator"
    COMMUNITY = "community", "Community"
    FINANCIER = "financier", "Financier"
    COMMERCIAL = "commercial", "Commercial"
    NGO = "ngo", "NGO"
    INDUSTRY = "industry", "Industry"
    RESEARCH = "research", "Research"
    GOVERNMENT = "government", "Government"


SCORE_1_10 = [MinValueValidator(1), MaxValueValidator(10)]


class Stakeholder(TimeStampedModel):
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=StakeholderCategory.choices)
    influence = models.PositiveSmallIntegerField(validators=SCORE_1_10)
    interest = models.PositiveSmallIntegerField(validators=SCORE_1_10)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="governance_stakeholders")
    engagement_plan = models.TextField(blank=True)
    last_engaged_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-influence", "name"]


class MilestoneStatus(models.TextChoices):
    NOT_STARTED = "not_started", "Not started"
    ON_TRACK = "on_track", "On track"
    DELAYED = "delayed", "Delayed"
    COMPLETE = "complete", "Complete"


class MilestonePhase(models.TextChoices):
    DESIGN = "design", "Design"
    PILOT = "pilot", "Pilot"
    ROLLOUT = "rollout", "Rollout"
    REVIEW = "review", "Review"


class Milestone(TimeStampedModel):
    policy = models.ForeignKey(Policy, on_delete=models.PROTECT, related_name="milestones")
    name = models.CharField(max_length=255)
    phase = models.CharField(max_length=20, choices=MilestonePhase.choices)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="governance_milestones")
    start_date = models.DateField()
    end_date = models.DateField()
    progress = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    kpi = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=MilestoneStatus.choices, default=MilestoneStatus.NOT_STARTED, db_index=True)

    class Meta:
        ordering = ["start_date", "name"]


class ImpactAssessment(TimeStampedModel):
    """A regulatory impact assessment comparing policy scenarios."""

    reference = models.CharField(max_length=32, unique=True, default=assessment_reference, editable=False)
    title = models.CharField(max_length=255)
    policy = models.ForeignKey(Policy, on_delete=models.SET_NULL, null=True, blank=True, related_name="impact_assessments")
    currency = models.CharField(max_length=10, default="USD")
    unit = models.CharField(max_length=40, default="millions")
    horizon_years = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(50)])
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=8, validators=[MinValueValidator(0), MaxValueValidator(100)])
    assumptions = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]


IMPACT_CATEGORIES = ["economic", "social", "environmental", "institutional", "operational", "financial"]


class ImpactScenario(TimeStampedModel):
    assessment = models.ForeignKey(ImpactAssessment, on_delete=models.CASCADE, related_name="scenarios")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cost = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    benefit = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    # True when the figures come from verified data rather than estimates.
    verified = models.BooleanField(default=False)
    is_baseline = models.BooleanField(default=False)
    # One 1 to 5 score per IMPACT_CATEGORIES entry.
    scores = models.JSONField(default=dict, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "created_at"]


class Classification(models.TextChoices):
    INTERNAL = "internal", "Internal"
    RESTRICTED = "restricted", "Restricted"
    CONFIDENTIAL = "confidential", "Confidential"


class DocumentType(models.TextChoices):
    POLICY = "policy", "Governance policy"
    ASSESSMENT = "assessment", "Regulatory assessment"
    RISK_REPORT = "risk_report", "Risk report"
    COMPLIANCE_REPORT = "compliance_report", "Compliance report"
    AUDIT_FINDING = "audit_finding", "Audit finding"
    CORRESPONDENCE = "correspondence", "Correspondence"
    EVIDENCE = "evidence", "Supporting evidence"
    OTHER = "other", "Other"


def document_upload_to(instance, filename):
    return f"governance/documents/{timezone.now():%Y/%m}/{secrets.token_hex(8)}-{filename}"


class GovernanceDocument(TimeStampedModel):
    name = models.CharField(max_length=255)
    document_type = models.CharField(max_length=30, choices=DocumentType.choices, default=DocumentType.OTHER)
    classification = models.CharField(max_length=20, choices=Classification.choices, default=Classification.INTERNAL, db_index=True)
    file = models.FileField(upload_to=document_upload_to)
    original_filename = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)
    policy = models.ForeignKey(Policy, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    risk = models.ForeignKey(Risk, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    finding = models.ForeignKey(RegulatoryFinding, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at"]
