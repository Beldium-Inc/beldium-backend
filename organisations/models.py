import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q

from common.models import TimeStampedModel


class OrganisationType(models.TextChoices):
    MINING_COMPANY = "mining_company", "Mining company"
    COMPLIANCE_PARTNER = "compliance_partner", "Compliance partner"
    REGULATOR = "regulator", "Regulator"
    LABORATORY = "laboratory", "Laboratory"
    INSPECTION_BODY = "inspection_body", "Inspection body"


class Organisation(TimeStampedModel):
    name = models.CharField(max_length=255)
    organisation_type = models.CharField(max_length=30, choices=OrganisationType.choices, db_index=True)
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


class OrganisationMembership(TimeStampedModel):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organisation_memberships")
    role = models.CharField(max_length=20, choices=MembershipRole.choices, default=MembershipRole.MEMBER)
    title = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organisation", "user"], name="unique_organisation_member")]

    def __str__(self):
        return f"{self.user.email} - {self.organisation.name}"


class OrganisationInvitation(TimeStampedModel):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=MembershipRole.choices, default=MembershipRole.MEMBER)
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
    requested_role = models.CharField(max_length=20, choices=MembershipRole.choices, default=MembershipRole.MEMBER)
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
