"""Logistics records are owned by organisations; review grants are explicit."""
import secrets

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def company_reference():
    return f'BLD-LOG-{timezone.now().year}-{secrets.token_hex(5).upper()}'


def short_reference(prefix):
    return f'{prefix}-{timezone.now():%Y%m%d}-{secrets.token_hex(3).upper()}'


def transport_request_reference():
    return short_reference('TR')


def movement_reference():
    return short_reference('MOV')


def delivery_reference():
    return short_reference('DEL')


def payment_reference():
    return short_reference('PAY')


def incident_reference():
    return short_reference('INC')


def operations_document_reference():
    return short_reference('DOC')


def compliance_finding_reference():
    return short_reference('NC')


class Domain(models.TextChoices):
    CORPORATE = 'corporate', 'Corporate verification'
    REGULATORY = 'regulatory', 'Regulatory licensing'
    FLEET = 'fleet', 'Fleet compliance'
    DRIVER = 'driver', 'Driver compliance'
    INSURANCE = 'insurance', 'Insurance cover'
    HS = 'hs', 'Health and safety'
    OPERATIONAL = 'operational', 'Operational capability'
    MINERAL = 'mineral', 'Mineral transport'
    DATA = 'data', 'Data and platform'


class ApplicationStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    SUBMITTED = 'submitted', 'Submitted'
    UNDER_REVIEW = 'under_review', 'Under review'
    AWAITING_INFORMATION = 'awaiting_information', 'Awaiting information'
    CONDITIONAL = 'conditionally_approved', 'Conditionally approved'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Rejected'


class EvidenceStatus(models.TextChoices):
    PENDING = 'pending', 'Pending review'
    VERIFIED = 'verified', 'Verified'
    REJECTED = 'rejected', 'Rejected'


class DeliveryStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    IN_TRANSIT = 'in_transit', 'In transit'
    ARRIVED = 'arrived', 'Arrived'
    COMPLETED = 'completed', 'Completed'
    VARIANCE_FLAGGED = 'variance_flagged', 'Variance flagged'
    CANCELLED = 'cancelled', 'Cancelled'


class PaymentStatus(models.TextChoices):
    NOT_INVOICED = 'not_invoiced', 'Not invoiced'
    INVOICE_RAISED = 'invoice_raised', 'Invoice raised'
    SUBMITTED = 'submitted', 'Submitted'
    PART_PAID = 'part_paid', 'Part paid'
    PAID = 'paid', 'Paid'
    OVERDUE = 'overdue', 'Overdue'


class IncidentStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    UNDER_INVESTIGATION = 'under_investigation', 'Under investigation'
    RESOLVED = 'resolved', 'Resolved'
    CLOSED = 'closed', 'Closed'


class OperationsDocumentStatus(models.TextChoices):
    ACTION_REQUIRED = 'action_required', 'Action required'
    UNDER_REVIEW = 'under_review', 'Under review'
    VERIFIED = 'verified', 'Verified'
    EXPIRING = 'expiring', 'Expiring'
    EXPIRED = 'expired', 'Expired'
    REJECTED = 'rejected', 'Rejected'


class ComplianceFindingStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    UNDER_REVIEW = 'under_review', 'Under review'
    CORRECTIVE_ACTION_SUBMITTED = 'corrective_action_submitted', 'Corrective action submitted'
    CLEARED = 'cleared', 'Cleared'


class LogisticsCompany(TimeStampedModel):
    organisation = models.OneToOneField('organisations.Organisation', on_delete=models.PROTECT, related_name='logistics_company')
    reference = models.CharField(max_length=50, unique=True, default=company_reference, editable=False)
    contact_name = models.CharField(max_length=200)
    contact_email = models.EmailField()
    contact_phone = models.CharField(max_length=30)
    incorporated_on = models.DateField(null=True, blank=True)
    employees = models.PositiveIntegerField(default=0)
    annual_tonnage = models.DecimalField(max_digits=15, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    services = models.JSONField(default=list)

    class Meta:
        ordering = ['organisation__name']


class LogisticsAccessGrant(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='access_grants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[('reviewer', 'Reviewer'), ('regulator', 'Regulator')])
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['company', 'user', 'role'], name='unique_logistics_access_grant')]


class OperatingLocation(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='locations')
    name = models.CharField(max_length=200)
    location_type = models.CharField(max_length=80)
    address = models.TextField()
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=100, default='Nigeria')
    staff_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['name']


class Vehicle(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='vehicles')
    registration = models.CharField(max_length=40, unique=True)
    vin = models.CharField(max_length=40, unique=True)
    vehicle_type = models.CharField(max_length=100)
    make = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    year = models.PositiveSmallIntegerField(validators=[MinValueValidator(1900)])
    capacity = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    capacity_unit = models.CharField(max_length=20, choices=[('tonnes', 'Tonnes'), ('litres', 'Litres'), ('seats', 'Seats')])
    ownership = models.CharField(max_length=20, choices=[('owned', 'Owned'), ('leased', 'Leased'), ('contracted', 'Contracted')])
    insurer = models.CharField(max_length=200, blank=True)
    insurance_expiry = models.DateField()
    roadworthiness_expiry = models.DateField()
    gps_status = models.CharField(max_length=20, choices=[('unknown', 'Unknown'), ('active', 'Active'), ('intermittent', 'Intermittent'), ('inactive', 'Inactive')], default='unknown')
    location = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['registration']


class Driver(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='drivers')
    full_name = models.CharField(max_length=200)
    licence_number = models.CharField(max_length=100, unique=True)
    licence_class = models.CharField(max_length=100)
    licence_expiry = models.DateField()
    national_id = models.CharField(max_length=100, blank=True)
    years_experience = models.PositiveSmallIntegerField(default=0)
    assigned_vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_drivers')
    training = models.JSONField(default=list)
    medical_expiry = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['full_name']


class LogisticsApplication(TimeStampedModel):
    company = models.OneToOneField(LogisticsCompany, on_delete=models.PROTECT, related_name='application')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='logistics_reviews')
    status = models.CharField(max_length=30, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rationale = models.TextField(blank=True)
    # The initial policy is an explicit internal baseline, not a legal certification.
    policy_version = models.CharField(max_length=100, default='logistics-baseline-v1')
    domain_weights = models.JSONField(default=dict)

    class Meta:
        ordering = ['-created_at']


class DomainReview(TimeStampedModel):
    application = models.ForeignKey(LogisticsApplication, on_delete=models.CASCADE, related_name='sections')
    key = models.CharField(max_length=30, choices=Domain.choices)
    data = models.JSONField(default=dict)
    applicable = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('passed', 'Passed'), ('attention', 'Attention'), ('failed', 'Failed')], default='pending')
    score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['application', 'key'], name='unique_logistics_domain')]
        ordering = ['key']


class ApprovalCondition(TimeStampedModel):
    application = models.ForeignKey(LogisticsApplication, on_delete=models.CASCADE, related_name='conditions')
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField()
    service_scope = models.CharField(max_length=100, blank=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')


class LogisticsDocument(TimeStampedModel):
    application = models.ForeignKey(LogisticsApplication, on_delete=models.CASCADE, related_name='documents')
    domain = models.CharField(max_length=30, choices=Domain.choices)
    document_type = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=200, blank=True)
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    service_scope = models.CharField(max_length=100, blank=True)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, null=True, blank=True)
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, null=True, blank=True)
    condition = models.ForeignKey(ApprovalCondition, on_delete=models.PROTECT, null=True, blank=True, related_name='documents')
    file = models.FileField(upload_to='logistics/evidence/%Y/%m/')
    original_name = models.CharField(max_length=255)
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=EvidenceStatus.choices, default='pending')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['application', 'document_type', 'version'], name='unique_logistics_document_version'),
            models.UniqueConstraint(fields=['application', 'document_type'], condition=models.Q(is_current=True), name='unique_current_logistics_document'),
        ]
        ordering = ['-created_at']


class DocumentNote(TimeStampedModel):
    document = models.ForeignKey(LogisticsDocument, on_delete=models.CASCADE, related_name='notes')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField()
    internal = models.BooleanField(default=False)


class InformationRequest(TimeStampedModel):
    application = models.ForeignKey(LogisticsApplication, on_delete=models.CASCADE, related_name='requests')
    reason = models.CharField(max_length=200)
    message = models.TextField()
    items = models.JSONField(default=list)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=[('open', 'Open'), ('responded', 'Responded'), ('accepted', 'Accepted')], default='open')
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    review_notes = models.TextField(blank=True)


class RequestResponse(TimeStampedModel):
    request = models.ForeignKey(InformationRequest, on_delete=models.CASCADE, related_name='responses')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    message = models.TextField()
    documents = models.ManyToManyField(LogisticsDocument)

    class Meta:
        ordering = ['created_at']


class ScopeRestriction(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='restrictions')
    service_scope = models.CharField(max_length=100)
    reason = models.TextField()
    source_document = models.ForeignKey(LogisticsDocument, on_delete=models.PROTECT, null=True, blank=True)
    source_condition = models.ForeignKey(ApprovalCondition, on_delete=models.PROTECT, null=True, blank=True)
    automatic = models.BooleanField(default=False)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    resolution_notes = models.TextField(blank=True)


class MonitoringEvent(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE)
    event_key = models.CharField(max_length=255, unique=True)
    kind = models.CharField(max_length=40)
    message = models.TextField()
    document = models.ForeignKey(LogisticsDocument, on_delete=models.CASCADE, null=True, blank=True)


class TransportRequest(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='transport_requests')
    reference = models.CharField(max_length=40, unique=True, default=transport_request_reference, editable=False)
    rfq_id = models.CharField(max_length=60, blank=True, db_index=True)
    transaction_id = models.CharField(max_length=60, blank=True, db_index=True)
    movement_type = models.CharField(max_length=40, db_index=True)
    requester = models.CharField(max_length=200)
    miner = models.CharField(max_length=200, blank=True, db_index=True)
    buyer = models.CharField(max_length=200, blank=True, db_index=True)
    mineral = models.CharField(max_length=120, blank=True, db_index=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    quantity_unit = models.CharField(max_length=20, default='MT')
    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    required_pickup_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=30, choices=[('new', 'New'), ('accepted', 'Accepted'), ('assigned', 'Assigned'), ('blocked', 'Blocked'), ('cancelled', 'Cancelled')], default='new', db_index=True)
    blocked_reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['required_pickup_at', '-created_at']
        indexes = [models.Index(fields=['company', 'status', 'movement_type'])]


class Movement(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='movements')
    reference = models.CharField(max_length=40, unique=True, default=movement_reference, editable=False)
    request = models.ForeignKey(TransportRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    batch_id = models.CharField(max_length=80, blank=True, db_index=True)
    rfq_id = models.CharField(max_length=60, blank=True, db_index=True)
    transaction_id = models.CharField(max_length=60, blank=True, db_index=True)
    movement_type = models.CharField(max_length=40, db_index=True)
    miner = models.CharField(max_length=200, blank=True, db_index=True)
    buyer = models.CharField(max_length=200, blank=True, db_index=True)
    mineral = models.CharField(max_length=120, db_index=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(0)])
    quantity_unit = models.CharField(max_length=20, default='MT')
    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    driver = models.ForeignKey(Driver, on_delete=models.SET_NULL, null=True, blank=True, related_name='movements')
    pickup_at = models.DateTimeField(null=True, blank=True, db_index=True)
    eta_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=30, choices=[('scheduled', 'Scheduled'), ('assigned', 'Assigned'), ('loading', 'Loading'), ('in_transit', 'In transit'), ('delayed', 'Delayed'), ('delivered', 'Delivered'), ('cancelled', 'Cancelled')], db_index=True)
    last_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_gps_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-pickup_at', '-created_at']
        indexes = [models.Index(fields=['company', 'status', 'movement_type']), models.Index(fields=['company', 'miner', 'buyer'])]


class Delivery(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='deliveries')
    reference = models.CharField(max_length=40, unique=True, default=delivery_reference, editable=False)
    movement = models.ForeignKey(Movement, on_delete=models.CASCADE, related_name='deliveries')
    destination_type = models.CharField(max_length=40, db_index=True)
    destination = models.CharField(max_length=255)
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    quantity_unit = models.CharField(max_length=20, default='MT')
    receipt_reference = models.CharField(max_length=80, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    custody_transferred_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=40, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-arrived_at', '-created_at']


class LogisticsTransaction(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='operations_transactions')
    transaction_id = models.CharField(max_length=60, unique=True)
    rfq_id = models.CharField(max_length=60, blank=True, db_index=True)
    buyer = models.CharField(max_length=200, db_index=True)
    miner = models.CharField(max_length=200, db_index=True)
    material = models.CharField(max_length=150, db_index=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    quantity_unit = models.CharField(max_length=20, default='MT')
    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    movement = models.ForeignKey(Movement, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    transport_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    stage = models.CharField(max_length=120, db_index=True)
    delivery_status = models.CharField(max_length=60, db_index=True)
    payment_status = models.CharField(max_length=60, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']


class LogisticsPayment(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='operations_payments')
    reference = models.CharField(max_length=40, unique=True, default=payment_reference, editable=False)
    transaction = models.ForeignKey(LogisticsTransaction, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    movement = models.ForeignKey(Movement, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    invoice_reference = models.CharField(max_length=80, blank=True, db_index=True)
    job_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    due_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=40, choices=PaymentStatus.choices, default=PaymentStatus.NOT_INVOICED, db_index=True)
    payment_date = models.DateField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']


class Incident(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='incidents')
    reference = models.CharField(max_length=40, unique=True, default=incident_reference, editable=False)
    movement = models.ForeignKey(Movement, on_delete=models.SET_NULL, null=True, blank=True, related_name='incidents')
    incident_type = models.CharField(max_length=60, db_index=True)
    severity = models.CharField(max_length=20, choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')], db_index=True)
    status = models.CharField(max_length=40, choices=IncidentStatus.choices, default=IncidentStatus.OPEN, db_index=True)
    location = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    description = models.TextField()
    immediate_action = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-occurred_at']


class OperationsDocument(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='operations_documents')
    reference = models.CharField(max_length=40, unique=True, default=operations_document_reference, editable=False)
    name = models.CharField(max_length=200)
    document_type = models.CharField(max_length=80, db_index=True)
    related_asset = models.CharField(max_length=200, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    verification_status = models.CharField(max_length=40, choices=OperationsDocumentStatus.choices, default=OperationsDocumentStatus.UNDER_REVIEW, db_index=True)
    compliance_status = models.CharField(max_length=40, choices=OperationsDocumentStatus.choices, default=OperationsDocumentStatus.UNDER_REVIEW, db_index=True)
    file = models.FileField(upload_to='logistics/operations/%Y/%m/', null=True, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['expiry_date', 'name']


class ComplianceFinding(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='operations_compliance_findings')
    reference = models.CharField(max_length=40, unique=True, default=compliance_finding_reference, editable=False)
    area = models.CharField(max_length=80, db_index=True)
    detail = models.TextField()
    action = models.TextField(blank=True)
    owner = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=40, choices=ComplianceFindingStatus.choices, default=ComplianceFindingStatus.OPEN, db_index=True)
    raised_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-raised_at']


class QualityRecord(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='quality_records')
    transaction_id = models.CharField(max_length=60, db_index=True)
    sample_status = models.CharField(max_length=160)
    result = models.CharField(max_length=160, blank=True)
    approval = models.CharField(max_length=80, db_index=True)
    handling = models.CharField(max_length=200, blank=True)
    certificate = models.CharField(max_length=80, blank=True)
    buyer_acceptance = models.CharField(max_length=160, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']


class OperationsEvent(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='operations_events')
    occurred_at = models.DateTimeField(db_index=True)
    text = models.TextField()
    sector = models.CharField(max_length=60, db_index=True)
    event_type = models.CharField(max_length=60, blank=True, db_index=True)
    unread = models.BooleanField(default=False, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-occurred_at', '-created_at']


class ActionItem(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE, related_name='action_items')
    action = models.CharField(max_length=160)
    target = models.CharField(max_length=255)
    urgency = models.CharField(max_length=80, db_index=True)
    status = models.CharField(max_length=30, choices=[('open', 'Open'), ('done', 'Done'), ('dismissed', 'Dismissed')], default='open', db_index=True)
    related_movement = models.ForeignKey(Movement, on_delete=models.SET_NULL, null=True, blank=True, related_name='action_items')
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['status', '-created_at']


class Notification(TimeStampedModel):
    company = models.ForeignKey(LogisticsCompany, on_delete=models.CASCADE)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    body = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class LogisticsReport(TimeStampedModel):
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    report_type = models.CharField(max_length=30, default='register')
    company_ids = models.JSONField(default=list)
    content = models.TextField()

    class Meta:
        ordering = ['-created_at']
