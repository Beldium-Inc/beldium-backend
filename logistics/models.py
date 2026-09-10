"""Logistics records are owned by organisations; review grants are explicit."""
import secrets

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def company_reference():
    return f'BLD-LOG-{timezone.now().year}-{secrets.token_hex(5).upper()}'


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
