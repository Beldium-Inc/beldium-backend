import secrets
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def seller_reference():
    return f"BLD-MKT-SELLER-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def product_reference():
    return f"BLD-MKT-PROD-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def order_reference():
    return f"BLD-MKT-ORD-{timezone.now().year}-{secrets.token_hex(4).upper()}"


class SellerStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_REVIEW = "pending_review", "Pending review"
    VERIFIED = "verified", "Verified"
    RESTRICTED = "restricted", "Restricted"
    SUSPENDED = "suspended", "Suspended"
    REJECTED = "rejected", "Rejected"


class ListingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_REVIEW = "pending_review", "Pending review"
    ACTIVE = "active", "Active"
    CHANGES_REQUIRED = "changes_required", "Changes required"
    RESTRICTED = "restricted", "Restricted"
    SUSPENDED = "suspended", "Suspended"
    ARCHIVED = "archived", "Archived"


class ProductCategory(models.TextChoices):
    ORE = "ore", "Ore"
    CONCENTRATE = "concentrate", "Concentrate"
    REFINED_METAL = "refined_metal", "Refined metal"
    GEMSTONE = "gemstone", "Gemstone"
    EQUIPMENT = "equipment", "Equipment"
    SERVICE = "service", "Service"


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PASSED = "passed", "Passed"
    ATTENTION = "attention", "Attention"
    FAILED = "failed", "Failed"


class EvidenceStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class OrderStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_PAYMENT = "pending_payment", "Pending payment"
    PAID = "paid", "Paid"
    FULFILLING = "fulfilling", "Fulfilling"
    SHIPPED = "shipped", "Shipped"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    DISPUTED = "disputed", "Disputed"


class PaymentStatus(models.TextChoices):
    NOT_REQUIRED = "not_required", "Not required"
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class SellerProfile(TimeStampedModel):
    organisation = models.OneToOneField("organisations.Organisation", on_delete=models.PROTECT, related_name="marketplace_seller")
    reference = models.CharField(max_length=50, unique=True, default=seller_reference, editable=False)
    status = models.CharField(max_length=30, choices=SellerStatus.choices, default=SellerStatus.DRAFT, db_index=True)
    display_name = models.CharField(max_length=255)
    contact_name = models.CharField(max_length=200)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30)
    business_address = models.TextField(blank=True)
    description = models.TextField(blank=True)
    service_regions = models.JSONField(default=list)
    accepted_terms_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    trust_score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])

    class Meta:
        ordering = ["display_name"]


class MarketplaceAccessGrant(TimeStampedModel):
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name="access_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[("reviewer", "Reviewer"), ("regulator", "Regulator"), ("support", "Support")])
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["seller", "user", "role"], name="unique_marketplace_access_grant")]


class MarketplaceProduct(TimeStampedModel):
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name="products")
    reference = models.CharField(max_length=50, unique=True, default=product_reference, editable=False)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=30, choices=ProductCategory.choices)
    mineral_type = models.CharField(max_length=100, blank=True)
    origin_country = models.CharField(max_length=100, default="Nigeria")
    origin_state = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    quantity_available = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=30, default="tonnes")
    price = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=30, choices=ListingStatus.choices, default=ListingStatus.DRAFT, db_index=True)
    compliance_score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    risk_band = models.CharField(max_length=20, default="high")
    published_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]


class ProductDocument(TimeStampedModel):
    product = models.ForeignKey(MarketplaceProduct, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=200, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="marketplace/evidence/%Y/%m/")
    original_name = models.CharField(max_length=255)
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=EvidenceStatus.choices, default=EvidenceStatus.PENDING)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["product", "document_type", "version"], name="unique_marketplace_document_version"),
            models.UniqueConstraint(fields=["product", "document_type"], condition=models.Q(is_current=True), name="unique_current_marketplace_document"),
        ]


class ProductComplianceCheck(TimeStampedModel):
    product = models.ForeignKey(MarketplaceProduct, on_delete=models.CASCADE, related_name="checks")
    key = models.CharField(max_length=40)
    label = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [models.UniqueConstraint(fields=["product", "key"], name="unique_marketplace_check")]


class MarketplaceOrder(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=order_reference, editable=False)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="marketplace_orders")
    buyer_organisation = models.ForeignKey("organisations.Organisation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    seller = models.ForeignKey(SellerProfile, on_delete=models.PROTECT, related_name="orders")
    product = models.ForeignKey(MarketplaceProduct, on_delete=models.PROTECT, related_name="orders")
    quantity = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(max_length=30, choices=OrderStatus.choices, default=OrderStatus.PENDING_PAYMENT, db_index=True)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    payment_reference = models.CharField(max_length=120, blank=True)
    escrow_reference = models.CharField(max_length=120, blank=True)
    delivery_address = models.TextField(blank=True)
    shipping_reference = models.CharField(max_length=120, blank=True)
    placed_at = models.DateTimeField(auto_now_add=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MarketplaceLicense(TimeStampedModel):
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name="licenses")
    product = models.ForeignKey(MarketplaceProduct, on_delete=models.CASCADE, null=True, blank=True, related_name="licenses")
    license_type = models.CharField(max_length=100)
    issuer = models.CharField(max_length=200)
    license_number = models.CharField(max_length=120)
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=EvidenceStatus.choices, default=EvidenceStatus.PENDING)

    class Meta:
        ordering = ["expires_on", "license_type"]


class MarketplaceDispute(TimeStampedModel):
    order = models.ForeignKey(MarketplaceOrder, on_delete=models.CASCADE, related_name="disputes")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reason = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=[("open", "Open"), ("under_review", "Under review"), ("resolved", "Resolved")], default="open")
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MarketplaceNotification(TimeStampedModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, null=True, blank=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MarketplaceAuditEvent(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, null=True, blank=True)
    product = models.ForeignKey(MarketplaceProduct, on_delete=models.CASCADE, null=True, blank=True)
    order = models.ForeignKey(MarketplaceOrder, on_delete=models.CASCADE, null=True, blank=True)
    event_type = models.CharField(max_length=80, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MarketplaceReport(TimeStampedModel):
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    report_type = models.CharField(max_length=30, default="marketplace")
    content = models.TextField()

    class Meta:
        ordering = ["-created_at"]


class PaymentWebhookEvent(TimeStampedModel):
    provider = models.CharField(max_length=50)
    event_id = models.CharField(max_length=120)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="unique_marketplace_webhook_event")]
