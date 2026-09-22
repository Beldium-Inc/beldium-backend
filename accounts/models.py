import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

from common.models import TimeStampedModel


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser enabled")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class OnboardingRole(models.TextChoices):
        COMPLIANCE_ORGANISATION = "compliance-org", "Mining Compliance Organisation"
        COMPLIANCE_OFFICER = "compliance-officer", "Mining Compliance Officer"
        REGULATORY_ORGANISATION = "regulatory-org", "Regulatory / Oversight Organisation"
        REGULATORY_OFFICER = "regulatory-officer", "Regulatory / Oversight Officer"
        INDEPENDENT = "independent", "Independent Mining Compliance Professional"

    class Portal(models.TextChoices):
        COMPLIANCE = "compliance", "Compliance"
        MINER = "miner", "Miner Hub"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=100, blank=True)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    onboarding_role = models.CharField(max_length=40, choices=OnboardingRole.choices, blank=True)
    # Which frontend this account registered through. Blank means a legacy
    # account predating this field (imported, or registered before either
    # frontend was sending it) — those are grandfathered through the login
    # check in VerifiedTokenObtainPairSerializer rather than locked out.
    portal = models.CharField(max_length=20, choices=Portal.choices, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        ordering = ["-created_at"]

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def __str__(self):
        return self.email


class EmailVerificationCode(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verification_codes")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField(db_index=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "consumed_at", "expires_at"])]

    def __str__(self):
        return f"Email verification for {self.user.email}"


class PhoneVerificationCode(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="phone_verification_codes")
    phone_number = models.CharField(max_length=30)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField(db_index=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "consumed_at", "expires_at"])]


class AccountRecoveryCode(TimeStampedModel):
    class Purpose(models.TextChoices):
        PASSWORD_RESET = "password_reset", "Password reset"
        EMAIL_CHANGE = "email_change", "Email change"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="account_recovery_codes")
    purpose = models.CharField(max_length=20, choices=Purpose.choices, db_index=True)
    target_email = models.EmailField(blank=True)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField(db_index=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "consumed_at", "expires_at"])]


class AccountAuditEvent(TimeStampedModel):
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_audit_events",
    )
    event_type = models.CharField(max_length=80, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class SocialIdentity(TimeStampedModel):
    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        MICROSOFT = "microsoft", "Microsoft"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="social_identities")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    subject = models.CharField(max_length=255)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "subject"], name="unique_social_identity")]
