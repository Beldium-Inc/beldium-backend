import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from common.models import TimeStampedModel


def generate_beldium_id():
    return f"BLD-ORG-{timezone.now().year}-{secrets.token_hex(4).upper()}"


# Letters and digits that cannot be misread for each other when a code is
# read out or typed by hand (no 0/O, 1/I/L).
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 8


def generate_join_code():
    return "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))


def normalise_join_code(value):
    """Accept the code the way people type it: any case, with spaces or a dash."""
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


class OrganisationType(models.TextChoices):
    MINING_COMPANY = "mining_company", "Mining company"
    LOGISTICS_COMPANY = "logistics_company", "Logistics company"
    COMPLIANCE_PARTNER = "compliance_partner", "Compliance partner"
    REGULATOR = "regulator", "Regulator"
    LABORATORY = "laboratory", "Laboratory"
    INSPECTION_BODY = "inspection_body", "Inspection body"
    # The sector's own companies, registered through the participant
    # application (backend plan BE-O3).
    PROCESSING_COMPANY = "processing_company", "Processing company"
    WAREHOUSE_OPERATOR = "warehouse_operator", "Warehouse operator"
    EXPORTER = "exporter", "Exporter"
    MARKET_PARTICIPANT = "market_participant", "Market participant"


class ParticipantSubtype(models.TextChoices):
    """What kind of market participant a marketplace company is."""
    BUYER = "buyer", "Buyer"
    OFFTAKER = "offtaker", "Offtaker"
    OEM = "oem", "OEM"


# The compliance sectors. Kept here so organisations does not import the
# compliance app; compliance.models.Sector holds the same slugs.
SECTOR_CHOICES = [
    ("mining", "Mining"),
    ("processing", "Processing"),
    ("export", "Export"),
    ("quality", "Quality & Control"),
    ("warehousing", "Warehousing"),
    ("logistics", "Logistics"),
    ("marketplace", "Marketplace"),
]
SECTOR_SLUGS = [slug for slug, _ in SECTOR_CHOICES]

# The sector each of the sector's own company types belongs to.
SECTOR_FOR_TYPE = {
    OrganisationType.MINING_COMPANY: "mining",
    OrganisationType.LOGISTICS_COMPANY: "logistics",
    OrganisationType.PROCESSING_COMPANY: "processing",
    OrganisationType.WAREHOUSE_OPERATOR: "warehousing",
    OrganisationType.EXPORTER: "export",
    OrganisationType.MARKET_PARTICIPANT: "marketplace",
}


class Organisation(TimeStampedModel):
    beldium_id = models.CharField(max_length=32, unique=True, null=True, blank=True, editable=False)

    # Shared by the organisation's administrators so colleagues can ask to
    # join. Knowing the code only lets someone send a request; an owner or
    # administrator still approves or declines it.
    join_code = models.CharField(max_length=16, unique=True, null=True, blank=True, editable=False)

    def save(self, *args, **kwargs):
        if not self.beldium_id:
            self.beldium_id = generate_beldium_id()
        if not self.join_code:
            self.join_code = generate_join_code()
        return super().save(*args, **kwargs)
    name = models.CharField(max_length=255)
    organisation_type = models.CharField(max_length=30, choices=OrganisationType.choices, db_index=True)
    # Only used for market participants: buyer, offtaker or OEM.
    participant_subtype = models.CharField(max_length=20, choices=ParticipantSubtype.choices, blank=True, default="")
    registration_number = models.CharField(max_length=100, blank=True, db_index=True)
    tax_identifier = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=100, default="Nigeria")
    state = models.CharField(max_length=100, blank=True)
    verification_status = models.CharField(
        max_length=20,
        choices=[("draft", "Draft"), ("under_review", "Under review"), ("verified", "Verified"), ("rejected", "Rejected"), ("suspended", "Suspended")],
        default="draft",
        db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_organisations",
    )
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "registration_number"],
                condition=~Q(registration_number=""),
                name="unique_org_registration_per_country",
            )
        ]

    def __str__(self):
        return self.name


class MembershipRole(models.TextChoices):
    OWNER = "owner", "Owner"
    ADMIN = "admin", "Administrator"
    REVIEWER = "reviewer", "Reviewer"
    INSPECTOR = "inspector", "Inspector"
    MEMBER = "member", "Member"
    COMPLIANCE_MANAGER = "compliance_manager", "Compliance Manager"
    MINING_COMPLIANCE_OFFICER = "mining_compliance_officer", "Mining Compliance Officer"
    MINING_ENGINEER = "mining_engineer", "Mining Engineer"
    GEOLOGIST = "geologist", "Geologist"
    ENVIRONMENTAL_SPECIALIST = "environmental_specialist", "Environmental Specialist"
    HSE_SPECIALIST = "hse_specialist", "HSE Specialist"
    LEGAL_REGULATORY_SPECIALIST = "legal_regulatory_specialist", "Legal / Regulatory Specialist"
    ANALYST = "analyst", "Analyst"
    READ_ONLY = "read_only", "Read Only User"
    OTHER = "other", "Other"


# Owner and administrator are the "admin" column of the section 2 rights
# table; every other role is "staff".
ADMIN_ROLES = (MembershipRole.OWNER, MembershipRole.ADMIN)


class OrganisationMembership(TimeStampedModel):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organisation_memberships")
    role = models.CharField(max_length=40, choices=MembershipRole.choices, default=MembershipRole.MEMBER)
    title = models.CharField(max_length=150, blank=True)
    employee_id = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    # How the member got in (backend plan BE-O2). Null for members who
    # registered the organisation or joined before these were recorded.
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organisation", "user"], name="unique_organisation_member")]

    @property
    def status(self):
        """active or suspended. A pending member is a JoinRequest, not a
        membership, and a removed member has no membership row."""
        if self.is_active:
            return "active"
        return "suspended"

    @property
    def is_admin(self):
        return self.role in ADMIN_ROLES

    def __str__(self):
        return f"{self.user.email} - {self.organisation.name}"


class OrganisationInvitation(TimeStampedModel):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=40, choices=MembershipRole.choices, default=MembershipRole.MEMBER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_organisation_invitations")
    token = models.CharField(max_length=64, unique=True, default=secrets.token_urlsafe, editable=False)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class JoinRequest(TimeStampedModel):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="join_requests")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organisation_join_requests")
    requested_role = models.CharField(max_length=40, choices=MembershipRole.choices, default=MembershipRole.MEMBER)
    job_title = models.CharField(max_length=150, blank=True)
    employee_id = models.CharField(max_length=100, blank=True)
    justification = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("cancelled", "Cancelled")],
        default="pending",
        db_index=True,
    )
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_join_requests")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "requester"],
                condition=Q(status="pending"),
                name="unique_pending_join_request",
            )
        ]


class SectorAccessGrant(TimeStampedModel):
    """Read-only access to one sector for a regulator organisation.

    Written when a Beldium operator approves a regulator access request
    (backend plan BE-O5). A regulator organisation that holds any grant row
    sees only the sectors with an active grant; see organisations.access.
    Never deleted: revoking sets is_active to False and records when.
    """
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="sector_grants")
    sector = models.CharField(max_length=20, choices=SECTOR_CHOICES, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sector"]
        constraints = [models.UniqueConstraint(fields=["organisation", "sector"], name="unique_sector_grant")]

    def __str__(self):
        return f"{self.organisation.name}: {self.sector}"
