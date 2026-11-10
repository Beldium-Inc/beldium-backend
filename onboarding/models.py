from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from common.models import TimeStampedModel
from organisations.models import SECTOR_CHOICES, Organisation, ParticipantSubtype


class Dashboard(models.TextChoices):
    APPLICANT = "applicant", "Applicant"
    PARTNER = "partner", "Compliance partner"


class OptionKind(models.TextChoices):
    REGISTER = "register", "Register a new company"
    JOIN = "join", "Join an existing company"
    REGULATORY = "regulatory", "Regulatory access"
    SPECIFIC = "specific", "Sector specific"


class OrgPath(models.TextChoices):
    NEW = "new", "New"
    EXISTING = "existing", "Existing"


class ExternalPortal(models.TextChoices):
    """Which portal an option links out to. The address itself comes from
    settings (MINER_PORTAL_URL, LOGISTICS_PORTAL_URL) so it can differ per
    environment without a data change."""
    NONE = "", "None"
    MINER = "miner", "Miner Portal"
    LOGISTICS = "logistics", "Logistics Portal"


class OnboardingDashboard(models.Model):
    """One side of a sector's onboarding screen (backend plan BE-O1)."""
    sector = models.CharField(max_length=20, choices=SECTOR_CHOICES, db_index=True)
    key = models.CharField(max_length=20, choices=Dashboard.choices)
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["sector", "order", "key"]
        constraints = [models.UniqueConstraint(fields=["sector", "key"], name="unique_onboarding_dashboard")]

    def __str__(self):
        return f"{self.sector}: {self.label}"


class OnboardingOption(models.Model):
    """One choice on a dashboard. Adding a sector specific item is a new row,
    not a code change."""
    dashboard = models.ForeignKey(OnboardingDashboard, on_delete=models.CASCADE, related_name="options")
    key = models.CharField(max_length=40)
    kind = models.CharField(max_length=20, choices=OptionKind.choices)
    label = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # The frontend's onboarding role, which picks the application flow
    # ("participant", "compliance-org", "regulator-org", ...).
    role = models.CharField(max_length=40)
    org_path = models.CharField(max_length=10, choices=OrgPath.choices, blank=True, default="")
    external_portal = models.CharField(max_length=20, choices=ExternalPortal.choices, blank=True, default="")
    external_label = models.CharField(max_length=120, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")
    order = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["dashboard", "order", "key"]
        constraints = [models.UniqueConstraint(fields=["dashboard", "key"], name="unique_onboarding_option")]

    def __str__(self):
        return f"{self.dashboard}: {self.label}"


class ParticipantApplication(TimeStampedModel):
    """The draft application of one of a sector's own companies (BE-O3).

    The sections are stored as the frontend collects them; see
    onboarding/participants.py for what each sector requires on submit.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"

    organisation = models.OneToOneField(Organisation, on_delete=models.CASCADE, related_name="participant_application")
    sector = models.CharField(max_length=20, choices=SECTOR_CHOICES, db_index=True)
    participant_subtype = models.CharField(max_length=20, choices=ParticipantSubtype.choices, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    company = models.JSONField(default=dict, blank=True)
    operations = models.JSONField(default=dict, blank=True)
    sites = models.JSONField(default=list, blank=True)
    representative = models.JSONField(default=dict, blank=True)
    declarations = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


UPLOAD_EXTENSIONS = ["pdf", "doc", "docx", "jpg", "jpeg", "png"]


class ParticipantDocument(TimeStampedModel):
    """One uploaded document, keyed by the title the sector asks for. A new
    upload under the same title replaces the file while the draft is open."""
    application = models.ForeignKey(ParticipantApplication, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="onboarding/participants/%Y/%m/", validators=[FileExtensionValidator(UPLOAD_EXTENSIONS)])
    original_name = models.CharField(max_length=255, blank=True, default="")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["title"]
        constraints = [models.UniqueConstraint(fields=["application", "title"], name="unique_participant_document_title")]


class RegulatorAccessRequest(TimeStampedModel):
    """A government regulator asks for read-only access to some sectors
    (BE-O5, D9). Nothing is visible until a Beldium operator approves it and
    picks the sectors; approval creates the regulator organisation."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="regulator_access_requests")
    agency_name = models.CharField(max_length=255)
    institution_type = models.CharField(max_length=120, blank=True, default="")
    official_email = models.EmailField()
    office = models.CharField(max_length=255)
    state = models.CharField(max_length=100)
    requester_title = models.CharField(max_length=150, blank=True, default="")
    requested_sectors = models.JSONField(default=list)
    authority_letter = models.FileField(
        upload_to="onboarding/regulators/%Y/%m/", blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    mandate = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    approved_sectors = models.JSONField(default=list, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    organisation = models.ForeignKey(Organisation, on_delete=models.SET_NULL, null=True, blank=True, related_name="regulator_requests")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["requester"], condition=models.Q(status="pending"),
                name="unique_pending_regulator_request",
            )
        ]
