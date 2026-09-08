from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from common.models import TimeStampedModel


class ApplicationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    UNDER_REVIEW = "under_review", "Under review"
    ACTION_REQUIRED = "action_required", "Action required"
    CONDITIONALLY_APPROVED = "conditionally_approved", "Conditionally approved"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


REQUIRED_DOCUMENTS = (
    ("certificate_of_incorporation", "Certificate of Incorporation"),
    ("cac_status_report", "CAC Status Report / Company Information"),
    ("tin_evidence", "TIN Evidence"),
    ("company_profile", "Company Profile"),
    ("mining_sector_registrations", "Relevant Mining Sector Registrations"),
    ("key_personnel_cvs", "Key Personnel CVs"),
    ("professional_licences", "Professional Licences"),
    ("professional_memberships", "Professional Memberships"),
    ("technical_qualifications", "Technical Qualifications"),
    ("relevant_certifications", "Relevant Certifications"),
    ("inspection_procedures", "Inspection Procedures"),
    ("quality_assurance_procedure", "Quality Assurance Procedure"),
    ("compliance_procedures", "Compliance Procedures"),
    ("conflict_of_interest_policy", "Conflict of Interest Policy"),
    ("code_of_conduct", "Code of Conduct"),
)


class ComplianceApplication(TimeStampedModel):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_compliance_applications")
    organisation = models.OneToOneField(
        "organisations.Organisation", on_delete=models.CASCADE, related_name="compliance_application"
    )
    status = models.CharField(max_length=30, choices=ApplicationStatus.choices, default=ApplicationStatus.DRAFT, db_index=True)
    organisation_profile = models.JSONField(default=dict, blank=True)
    representative = models.JSONField(default=dict, blank=True)
    services = models.JSONField(default=dict, blank=True)
    professional_capability = models.JSONField(default=dict, blank=True)
    inspection_capability = models.JSONField(default=dict, blank=True)
    conflict_declaration = models.JSONField(default=dict, blank=True)
    declaration = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reviewed_compliance_applications",
    )
    review_notes = models.TextField(blank=True)
    conditional_requirements = models.TextField(blank=True)


class Personnel(TimeStampedModel):
    application = models.ForeignKey(ComplianceApplication, on_delete=models.CASCADE, related_name="personnel")
    full_name = models.CharField(max_length=200)
    role = models.CharField(max_length=150)
    discipline = models.CharField(max_length=150, blank=True)
    qualification = models.CharField(max_length=200, blank=True)
    years_experience = models.PositiveSmallIntegerField(default=0)
    registration_number = models.CharField(max_length=150, blank=True)
    cv = models.FileField(upload_to="compliance/personnel/cv/%Y/%m/", blank=True, validators=[FileExtensionValidator(["pdf", "doc", "docx"])])
    certificate = models.FileField(upload_to="compliance/personnel/certificates/%Y/%m/", blank=True, validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])])


class ComplianceDocument(TimeStampedModel):
    due_date = models.DateField(null=True, blank=True)
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SUBMITTED = "submitted", "Submitted"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    application = models.ForeignKey(ComplianceApplication, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=100, db_index=True)
    title = models.CharField(max_length=200)
    file = models.FileField(
        upload_to="compliance/documents/%Y/%m/", blank=True,
        validators=[FileExtensionValidator(["pdf", "doc", "docx", "jpg", "jpeg", "png"])],
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="requested_compliance_documents")
    request_message = models.TextField(blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_compliance_documents")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["application", "document_type"], name="unique_application_document_type")]

    @property
    def original_name(self):
        return Path(self.file.name).name if self.file else ""


class ApplicationMessage(TimeStampedModel):
    application = models.ForeignKey(ComplianceApplication, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="compliance_messages")
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class MessageReadReceipt(TimeStampedModel):
    message = models.ForeignKey(ApplicationMessage, on_delete=models.CASCADE, related_name="read_receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "user"], name="unique_message_reader")]


class ApprovalCondition(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending evidence"
        SUBMITTED = "submitted", "Evidence submitted"
        REJECTED = "rejected", "Evidence rejected"
        CLEARED = "cleared", "Cleared"

    application = models.ForeignKey(ComplianceApplication, on_delete=models.CASCADE, related_name="conditions")
    title = models.CharField(max_length=200)
    description = models.TextField()
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="issued_approval_conditions")

    class Meta:
        ordering = ["due_date", "created_at"]


class ConditionEvidence(TimeStampedModel):
    condition = models.ForeignKey(ApprovalCondition, on_delete=models.CASCADE, related_name="evidence")
    file = models.FileField(upload_to="compliance/conditions/%Y/%m/", validators=[FileExtensionValidator(["pdf", "doc", "docx", "jpg", "jpeg", "png"])])
    notes = models.TextField(blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="submitted_condition_evidence")
    status = models.CharField(max_length=20, choices=[("submitted", "Submitted"), ("verified", "Verified"), ("rejected", "Rejected")], default="submitted")
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="reviewed_condition_evidence")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
