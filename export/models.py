import secrets

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def exporter_reference():
    return f"BLD-EXP-{timezone.now().year}-{secrets.token_hex(5).upper()}"


def shipment_reference():
    return f"SHP-{timezone.now().year}-{secrets.token_hex(4).upper()}"


class ApplicationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under review"
    AWAITING_INFORMATION = "awaiting_information", "Awaiting information"
    CONDITIONALLY_APPROVED = "conditionally_approved", "Conditionally approved"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Domain(models.TextChoices):
    EXPORTER = "exporter", "Exporter profile"
    PRODUCT = "product", "Product classification"
    BUYER = "buyer", "Buyer and destination"
    SHIPMENT = "shipment", "Shipment plan"
    CUSTOMS = "customs", "Customs documentation"
    QUALITY = "quality", "Quality certificate"
    LOGISTICS = "logistics", "Logistics handoff"
    FINANCE = "finance", "Finance and proceeds"


class EvidenceStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class Exporter(TimeStampedModel):
    organisation = models.OneToOneField("organisations.Organisation", on_delete=models.PROTECT, related_name="exporter_profile")
    reference = models.CharField(max_length=50, unique=True, default=exporter_reference, editable=False)
    contact_name = models.CharField(max_length=200)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30)
    export_license_number = models.CharField(max_length=100, blank=True)
    license_expires_on = models.DateField(null=True, blank=True)
    destinations = models.JSONField(default=list)
    product_categories = models.JSONField(default=list)

    class Meta:
        ordering = ["organisation__name"]


class ExportAccessGrant(TimeStampedModel):
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE, related_name="access_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[("reviewer", "Reviewer"), ("regulator", "Regulator")])
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exporter", "user", "role"], name="unique_export_access_grant")]


class Product(TimeStampedModel):
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    hs_code = models.CharField(max_length=20)
    origin_state = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=30, default="tonnes")
    annual_capacity = models.DecimalField(max_digits=15, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    controlled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["exporter", "hs_code", "name"], name="unique_export_product")]


class Buyer(TimeStampedModel):
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE, related_name="buyers")
    name = models.CharField(max_length=200)
    country = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    tax_identifier = models.CharField(max_length=100, blank=True)
    screening_status = models.CharField(max_length=20, choices=[("pending", "Pending"), ("cleared", "Cleared"), ("flagged", "Flagged")], default="pending")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]


class ExportApplication(TimeStampedModel):
    exporter = models.OneToOneField(Exporter, on_delete=models.PROTECT, related_name="application")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="export_reviews")
    status = models.CharField(max_length=30, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rationale = models.TextField(blank=True)
    policy_version = models.CharField(max_length=100, default="export-baseline-v1")
    domain_weights = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]


class DomainReview(TimeStampedModel):
    application = models.ForeignKey(ExportApplication, on_delete=models.CASCADE, related_name="sections")
    key = models.CharField(max_length=30, choices=Domain.choices)
    data = models.JSONField(default=dict)
    applicable = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=[("pending", "Pending"), ("passed", "Passed"), ("attention", "Attention"), ("failed", "Failed")], default="pending")
    score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application", "key"], name="unique_export_domain")]
        ordering = ["key"]


class Shipment(TimeStampedModel):
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE, related_name="shipments")
    reference = models.CharField(max_length=50, unique=True, default=shipment_reference, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="shipments")
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="shipments")
    destination_country = models.CharField(max_length=100)
    port_of_loading = models.CharField(max_length=120)
    port_of_discharge = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=30, default="tonnes")
    estimated_value = models.DecimalField(max_digits=15, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    expected_ship_date = models.DateField()
    status = models.CharField(max_length=20, choices=[("planned", "Planned"), ("ready", "Ready"), ("cleared", "Cleared"), ("shipped", "Shipped"), ("held", "Held"), ("cancelled", "Cancelled")], default="planned")
    # Compliance decision, recorded once a shipment's checklist and
    # non-conformities have been cleared by a reviewer.
    decision_outcome = models.CharField(max_length=25, blank=True, choices=[("cleared", "Cleared"), ("conditionally_cleared", "Conditionally cleared"), ("declined", "Declined")])
    decision_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decision_at = models.DateTimeField(null=True, blank=True)
    decision_rationale = models.TextField(blank=True)
    decision_conditions = models.TextField(blank=True)

    class Meta:
        ordering = ["-expected_ship_date", "-created_at"]


class ShipmentChecklistItem(TimeStampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="checklist")
    domain = models.CharField(max_length=30, choices=Domain.choices)
    label = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    state = models.CharField(max_length=10, choices=[("pass", "Pass"), ("open", "Open"), ("fail", "Fail")], default="open")

    class Meta:
        ordering = ["domain", "id"]


class ShipmentNonConformity(TimeStampedModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="non_conformities")
    domain = models.CharField(max_length=30, choices=Domain.choices)
    title = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    severity = models.CharField(max_length=10, choices=[("minor", "Minor"), ("major", "Major"), ("critical", "Critical")], default="major")
    status = models.CharField(max_length=15, choices=[("open", "Open"), ("responded", "Responded"), ("closed", "Closed")], default="open")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    response = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class ExportCondition(TimeStampedModel):
    application = models.ForeignKey(ExportApplication, on_delete=models.CASCADE, related_name="conditions")
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField()
    domain = models.CharField(max_length=30, choices=Domain.choices, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")


class ExportDocument(TimeStampedModel):
    application = models.ForeignKey(ExportApplication, on_delete=models.CASCADE, related_name="documents")
    domain = models.CharField(max_length=30, choices=Domain.choices)
    document_type = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=200, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, null=True, blank=True, related_name="documents")
    condition = models.ForeignKey(ExportCondition, on_delete=models.PROTECT, null=True, blank=True, related_name="documents")
    file = models.FileField(upload_to="export/evidence/%Y/%m/")
    original_name = models.CharField(max_length=255)
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=EvidenceStatus.choices, default=EvidenceStatus.PENDING)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["application", "document_type", "version"], name="unique_export_document_version"),
            models.UniqueConstraint(fields=["application", "document_type"], condition=models.Q(is_current=True), name="unique_current_export_document"),
        ]
        ordering = ["-created_at"]


class InformationRequest(TimeStampedModel):
    application = models.ForeignKey(ExportApplication, on_delete=models.CASCADE, related_name="requests")
    reason = models.CharField(max_length=200)
    message = models.TextField()
    items = models.JSONField(default=list)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=[("open", "Open"), ("responded", "Responded"), ("accepted", "Accepted")], default="open")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    review_notes = models.TextField(blank=True)


class RequestResponse(TimeStampedModel):
    request = models.ForeignKey(InformationRequest, on_delete=models.CASCADE, related_name="responses")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    message = models.TextField()
    documents = models.ManyToManyField(ExportDocument)

    class Meta:
        ordering = ["created_at"]


class Notification(TimeStampedModel):
    exporter = models.ForeignKey(Exporter, on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    title = models.CharField(max_length=200)
    body = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ExportReport(TimeStampedModel):
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    report_type = models.CharField(max_length=30, default="register")
    exporter_ids = models.JSONField(default=list)
    content = models.TextField()

    class Meta:
        ordering = ["-created_at"]
