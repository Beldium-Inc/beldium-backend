import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def warehouse_reference():
    return f"BLD-WRH-{timezone.now().year}-{secrets.token_hex(5).upper()}"


def lot_reference():
    return f"LOT-{timezone.now().year}-{secrets.token_hex(4).upper()}"


class ApplicationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under review"
    AWAITING_INFORMATION = "awaiting_information", "Awaiting information"
    CONDITIONALLY_APPROVED = "conditionally_approved", "Conditionally approved"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class Domain(models.TextChoices):
    OPERATOR = "operator", "Warehouse operator"
    FACILITY = "facility", "Facility and location"
    STORAGE = "storage", "Storage controls"
    INVENTORY = "inventory", "Inventory traceability"
    SAFETY = "safety", "Fire, HSE and security"
    QUALITY = "quality", "Quality assurance"
    EQUIPMENT = "equipment", "Equipment calibration"
    LOGISTICS = "logistics", "Inbound and outbound logistics"


class EvidenceStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class WarehouseOperator(TimeStampedModel):
    organisation = models.OneToOneField("organisations.Organisation", on_delete=models.PROTECT, related_name="warehouse_profile")
    reference = models.CharField(max_length=50, unique=True, default=warehouse_reference, editable=False)
    contact_name = models.CharField(max_length=200)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30)
    warehouse_license_number = models.CharField(max_length=100, blank=True)
    license_expires_on = models.DateField(null=True, blank=True)
    services = models.JSONField(default=list)
    storage_categories = models.JSONField(default=list)
    # Rich company profile (formerly only present in frontend mock data).
    trading_name = models.CharField(max_length=200, blank=True)
    company_type = models.CharField(max_length=150, blank=True)
    incorporated_on = models.DateField(null=True, blank=True)
    mineral_title = models.CharField(max_length=255, blank=True)
    head_office_address = models.TextField(blank=True)
    bankers = models.CharField(max_length=255, blank=True)
    annual_turnover = models.CharField(max_length=100, blank=True)
    staff_count = models.PositiveIntegerField(null=True, blank=True)
    directors = models.JSONField(default=list, blank=True, help_text="[{name, role, bvn_verified}]")
    shareholding = models.JSONField(default=list, blank=True, help_text="[{holder, percent}]")

    class Meta:
        ordering = ["organisation__name"]


class WarehousingAccessGrant(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="access_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[("reviewer", "Reviewer"), ("regulator", "Regulator")])
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["warehouse", "user", "role"], name="unique_warehousing_access_grant")]


class Facility(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="facilities")
    name = models.CharField(max_length=200)
    facility_type = models.CharField(max_length=80, choices=[("ambient", "Ambient"), ("cold_chain", "Cold chain"), ("bonded", "Bonded"), ("hazmat", "Hazardous materials"), ("yard", "Open yard")])
    address = models.TextField()
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default="Nigeria")
    capacity = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    capacity_unit = models.CharField(max_length=20, choices=[("tonnes", "Tonnes"), ("sqm", "Square metres"), ("pallets", "Pallets")], default="tonnes")
    fire_certificate_expires_on = models.DateField(null=True, blank=True)
    insurance_expires_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # Rich facility profile (formerly only present in frontend mock data).
    coordinates = models.CharField(max_length=100, blank=True)
    land_title = models.CharField(max_length=255, blank=True)
    built_area = models.CharField(max_length=150, blank=True)
    bay_count = models.PositiveIntegerField(null=True, blank=True)
    loading_dock_count = models.PositiveIntegerField(null=True, blank=True)
    weighbridge_details = models.TextField(blank=True)
    laboratory_details = models.TextField(blank=True)
    security_details = models.TextField(blank=True)
    fire_system_details = models.TextField(blank=True)
    facility_contact = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]


class StorageZone(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="zones")
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="zones")
    name = models.CharField(max_length=200)
    storage_type = models.CharField(max_length=80)
    temperature_min = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    temperature_max = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    capacity = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    capacity_unit = models.CharField(max_length=20, default="tonnes")
    restricted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["facility__name", "name"]
        constraints = [models.UniqueConstraint(fields=["facility", "name"], name="unique_warehouse_zone")]


class InventoryLot(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="lots")
    reference = models.CharField(max_length=50, unique=True, default=lot_reference, editable=False)
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="lots")
    zone = models.ForeignKey(StorageZone, on_delete=models.PROTECT, related_name="lots")
    product_name = models.CharField(max_length=200)
    batch_number = models.CharField(max_length=100)
    owner_name = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)], help_text="Declared quantity at intake.")
    actual_weighbridge_quantity = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)], help_text="Measured weighbridge quantity at intake, if weighed.")
    unit = models.CharField(max_length=30, default="tonnes")
    received_on = models.DateField()
    expires_on = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=[("received", "Received"), ("stored", "Stored"), ("quarantined", "Quarantined"), ("released", "Released"), ("dispatched", "Dispatched")], default="received")

    class Meta:
        ordering = ["-received_on", "-created_at"]

    @property
    def variance_percent(self):
        if self.actual_weighbridge_quantity is None or not self.quantity:
            return None
        return round(float((self.actual_weighbridge_quantity - self.quantity) / self.quantity) * 100, 2)


class Inspection(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="inspections")
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="inspections")
    inspection_type = models.CharField(max_length=80)
    inspected_on = models.DateField()
    inspector_name = models.CharField(max_length=200)
    outcome = models.CharField(max_length=20, choices=[("passed", "Passed"), ("attention", "Attention"), ("failed", "Failed")], default="passed")
    findings = models.TextField(blank=True)
    next_due_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-inspected_on"]


class Incident(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="incidents")
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, related_name="incidents")
    lot = models.ForeignKey(InventoryLot, on_delete=models.SET_NULL, null=True, blank=True, related_name="incidents")
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=[("safety", "Safety"), ("environmental", "Environmental"), ("security", "Security"), ("stock_integrity", "Stock integrity")])
    severity = models.CharField(max_length=10, choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], default="medium")
    occurred_on = models.DateField()
    location = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=[("open", "Open"), ("under_investigation", "Under investigation"), ("closed", "Closed")], default="open")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-occurred_on", "-created_at"]


class ReleaseRequest(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="release_requests")
    lot = models.ForeignKey(InventoryLot, on_delete=models.PROTECT, related_name="release_requests")
    requested_by_name = models.CharField(max_length=200)
    destination = models.CharField(max_length=255)
    declared_quantity = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    actual_weighbridge_quantity = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=30, default="tonnes")
    status = models.CharField(max_length=20, choices=[("pending", "Pending authorisation"), ("authorised", "Authorised"), ("declined", "Declined")], default="pending")
    decision_reason = models.TextField(blank=True)
    authorised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    authorised_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def variance_percent(self):
        if self.actual_weighbridge_quantity is None or not self.declared_quantity:
            return None
        return round(float((self.actual_weighbridge_quantity - self.declared_quantity) / self.declared_quantity) * 100, 2)


class MonitoringAlert(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="monitoring_alerts")
    facility = models.ForeignKey(Facility, on_delete=models.SET_NULL, null=True, blank=True, related_name="monitoring_alerts")
    message = models.TextField()
    severity = models.CharField(max_length=10, choices=[("info", "Info"), ("warning", "Warning"), ("critical", "Critical")], default="info")
    source = models.CharField(max_length=150, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Inspector(TimeStampedModel):
    name = models.CharField(max_length=200)
    region = models.CharField(max_length=150, blank=True)
    title = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]


class Certificate(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE, related_name="certificates")
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE, related_name="certificates")
    application = models.ForeignKey("WarehousingApplication", on_delete=models.SET_NULL, null=True, blank=True, related_name="certificates")
    scope = models.CharField(max_length=255, blank=True)
    issued_on = models.DateField()
    expires_on = models.DateField()
    status = models.CharField(max_length=20, choices=[("active", "Active"), ("conditional", "Conditional"), ("suspended", "Suspended"), ("expired", "Expired")], default="active")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-issued_on"]


class WarehousingApplication(TimeStampedModel):
    warehouse = models.OneToOneField(WarehouseOperator, on_delete=models.PROTECT, related_name="application")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="warehousing_reviews")
    status = models.CharField(max_length=30, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rationale = models.TextField(blank=True)
    policy_version = models.CharField(max_length=100, default="warehousing-baseline-v1")
    domain_weights = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]


class DomainReview(TimeStampedModel):
    application = models.ForeignKey(WarehousingApplication, on_delete=models.CASCADE, related_name="sections")
    key = models.CharField(max_length=30, choices=Domain.choices)
    data = models.JSONField(default=dict)
    applicable = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=[("pending", "Pending"), ("passed", "Passed"), ("attention", "Attention"), ("failed", "Failed")], default="pending")
    score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application", "key"], name="unique_warehousing_domain")]
        ordering = ["key"]


class WarehousingCondition(TimeStampedModel):
    application = models.ForeignKey(WarehousingApplication, on_delete=models.CASCADE, related_name="conditions")
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField()
    domain = models.CharField(max_length=30, choices=Domain.choices, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")


class WarehousingDocument(TimeStampedModel):
    application = models.ForeignKey(WarehousingApplication, on_delete=models.CASCADE, related_name="documents")
    domain = models.CharField(max_length=30, choices=Domain.choices)
    document_type = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=200, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    lot = models.ForeignKey(InventoryLot, on_delete=models.PROTECT, null=True, blank=True, related_name="documents")
    condition = models.ForeignKey(WarehousingCondition, on_delete=models.PROTECT, null=True, blank=True, related_name="documents")
    file = models.FileField(upload_to="warehousing/evidence/%Y/%m/")
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
            models.UniqueConstraint(fields=["application", "document_type", "version"], name="unique_warehousing_document_version"),
            models.UniqueConstraint(fields=["application", "document_type"], condition=models.Q(is_current=True), name="unique_current_warehousing_document"),
        ]
        ordering = ["-created_at"]


class InformationRequest(TimeStampedModel):
    application = models.ForeignKey(WarehousingApplication, on_delete=models.CASCADE, related_name="requests")
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
    documents = models.ManyToManyField(WarehousingDocument)

    class Meta:
        ordering = ["created_at"]


class Notification(TimeStampedModel):
    warehouse = models.ForeignKey(WarehouseOperator, on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    title = models.CharField(max_length=200)
    body = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class WarehousingReport(TimeStampedModel):
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    report_type = models.CharField(max_length=30, default="register")
    warehouse_ids = models.JSONField(default=list)
    content = models.TextField()

    class Meta:
        ordering = ["-created_at"]
