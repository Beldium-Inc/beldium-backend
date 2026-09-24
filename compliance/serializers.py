from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from accounts.models import AccountAuditEvent
from compliance.models import ApprovalCondition, ConditionEvidence, ApplicationMessage, ApplicationStatus, ComplianceApplication, ComplianceDocument, Personnel, REQUIRED_DOCUMENTS
from organisations.models import OrganisationType


MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg", "image/png",
}


def validate_upload(value):
    if value.size > MAX_UPLOAD_SIZE:
        raise serializers.ValidationError("File too large; the maximum size is 10 MB.")
    if getattr(value, "content_type", None) not in ALLOWED_CONTENT_TYPES:
        raise serializers.ValidationError("Only PDF, Word, JPEG, and PNG files are supported.")
    return value


class PersonnelSerializer(serializers.ModelSerializer):
    cv_url = serializers.SerializerMethodField()
    certificate_url = serializers.SerializerMethodField()

    class Meta:
        model = Personnel
        fields = ["id", "full_name", "role", "discipline", "qualification", "years_experience", "registration_number", "cv", "certificate", "cv_url", "certificate_url", "created_at", "updated_at"]
        read_only_fields = ["id", "cv_url", "certificate_url", "created_at", "updated_at"]
        extra_kwargs = {"cv": {"write_only": True, "required": False}, "certificate": {"write_only": True, "required": False}}

    def validate_cv(self, value):
        return validate_upload(value)

    def validate_certificate(self, value):
        return validate_upload(value)

    def _url(self, obj, field):
        # Point at the authenticated download action rather than the storage
        # URL: a local-storage path has nothing guarding it, and the API should
        # read the same whichever backend is configured.
        if not getattr(obj, field):
            return None
        url = reverse(
            "compliance-application-download-personnel-file",
            args=[obj.application_id, obj.id, field],
        )
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    @extend_schema_field(OpenApiTypes.URI)
    def get_cv_url(self, obj) -> str | None:
        return self._url(obj, "cv")

    @extend_schema_field(OpenApiTypes.URI)
    def get_certificate_url(self, obj) -> str | None:
        return self._url(obj, "certificate")


def validate_due_date(value):
    if value < timezone.localdate():
        raise serializers.ValidationError("The deadline cannot be in the past.")
    return value


class ComplianceDocumentSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()

    def get_is_overdue(self, obj) -> bool:
        return bool(obj.due_date and obj.due_date < timezone.localdate() and obj.status in {"requested", "rejected"})

    file_url = serializers.SerializerMethodField()
    original_name = serializers.CharField(read_only=True)

    class Meta:
        model = ComplianceDocument
        fields = ["id", "document_type", "title", "file", "file_url", "original_name", "status", "request_message", "due_date", "is_overdue", "review_notes", "reviewed_at", "created_at", "updated_at"]
        read_only_fields = ["id", "file_url", "original_name", "status", "request_message", "due_date", "is_overdue", "review_notes", "reviewed_at", "created_at", "updated_at"]
        extra_kwargs = {"file": {"write_only": True, "required": True}}

    def validate_file(self, value):
        return validate_upload(value)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        url = reverse(
            "compliance-application-download-document",
            args=[obj.application_id, obj.id],
        )
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ComplianceDocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceDocument
        fields = ["document_type", "file"]
        extra_kwargs = {"file": {"required": True}}

    def validate_file(self, value):
        return validate_upload(value)


class RequestedDocumentSerializer(serializers.ModelSerializer):
    def validate_due_date(self, value):
        return validate_due_date(value) if value else value

    class Meta:
        model = ComplianceDocument
        fields = ["id", "document_type", "title", "request_message", "due_date", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


class DocumentReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[ComplianceDocument.Status.VERIFIED, ComplianceDocument.Status.REJECTED])
    notes = serializers.CharField(required=False, allow_blank=True)


class ApplicationMessageSerializer(serializers.ModelSerializer):
    read_at = serializers.SerializerMethodField()
    is_internal = serializers.BooleanField(required=False, default=False)

    def get_read_at(self, obj) -> str | None:
        request = self.context.get("request")
        if not request:
            return None
        receipt = obj.read_receipts.filter(user=request.user).first()
        return receipt.read_at.isoformat() if receipt else None

    def validate_is_internal(self, value):
        if value and not self.context["request"].user.is_staff:
            raise serializers.ValidationError("Only staff may send internal messages.")
        return value

    author_email = serializers.EmailField(source="author.email", read_only=True)

    class Meta:
        model = ApplicationMessage
        fields = ["id", "author_email", "body", "is_internal", "read_at", "created_at"]
        read_only_fields = ["id", "author_email", "read_at", "created_at"]


class ConditionEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConditionEvidence
        fields = ["id", "file", "notes", "status", "submitted_by", "review_notes", "reviewed_by", "reviewed_at", "created_at"]
        read_only_fields = ["id", "status", "submitted_by", "review_notes", "reviewed_by", "reviewed_at", "created_at"]

    def validate_file(self, value):
        return validate_upload(value)


class ApprovalConditionSerializer(serializers.ModelSerializer):
    evidence = ConditionEvidenceSerializer(many=True, read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = ApprovalCondition
        fields = ["id", "title", "description", "due_date", "status", "is_overdue", "evidence", "created_at"]
        read_only_fields = ["id", "status", "is_overdue", "evidence", "created_at"]

    def validate_due_date(self, value):
        return validate_due_date(value)

    def get_is_overdue(self, obj) -> bool:
        return obj.due_date < timezone.localdate() and obj.status != ApprovalCondition.Status.CLEARED


ACTIVITY_DESCRIPTIONS = {
    # Keys here must match the metadata each record_account_event call actually
    # writes; a template naming a key that is not written falls back below.
    "compliance.application_created": "Application created",
    "compliance.section_saved": "Saved the {section} section",
    "compliance.personnel_added": "Added {full_name} to key personnel",
    "compliance.personnel_updated": "Updated {full_name}'s details",
    "compliance.personnel_removed": "Removed {full_name} from key personnel",
    "compliance.document_uploaded": "Uploaded {title}",
    "compliance.application_submitted": "Application submitted for review",
    "compliance.application_decided": "Review decision recorded: {status}",
    "compliance.document_requested": "Reviewer requested an additional document",
    "compliance.document_reviewed": "Document marked {status}",
    "compliance.condition_created": "Reviewer added an approval condition",
    "compliance.condition_evidence_submitted": "Evidence submitted for an approval condition",
    "compliance.condition_evidence_reviewed": "Condition evidence marked {status}",
}


class ApplicationActivitySerializer(serializers.ModelSerializer):
    """One entry in an application's activity feed, for its own members.

    AccountAuditEvent stores the actor's email, IP address and user agent. This
    is read by applicants, so none of those are serialised and the raw metadata
    is not passed through either — only a rendered sentence built from it.
    """

    actor = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()

    class Meta:
        model = AccountAuditEvent
        fields = ["id", "event_type", "actor", "description", "created_at"]

    def get_actor(self, obj) -> str:
        if obj.actor is None:
            return "System"
        # A reviewer is shown as the team. Naming them invites the applicant to
        # contact them directly and exposes staff identity on every decision.
        if obj.actor.is_staff:
            return "Beldium review team"
        return obj.actor.full_name or obj.actor.email

    def get_description(self, obj) -> str:
        metadata = obj.metadata or {}
        readable = {
            key: value.replace("_", " ").replace("-", " ") if isinstance(value, str) else value
            for key, value in metadata.items()
        }

        template = ACTIVITY_DESCRIPTIONS.get(obj.event_type)
        if not template:
            return obj.event_type.removeprefix("compliance.").replace("_", " ").capitalize()
        try:
            return template.format(**readable)
        except KeyError:
            # Older rows may predate a metadata key the template wants.
            return obj.event_type.removeprefix("compliance.").replace("_", " ").capitalize()


class ComplianceApplicationSerializer(serializers.ModelSerializer):
    conditions = ApprovalConditionSerializer(many=True, read_only=True)

    personnel = PersonnelSerializer(many=True, read_only=True)
    documents = ComplianceDocumentSerializer(many=True, read_only=True)
    progress = serializers.SerializerMethodField()
    # The organisation record's type, set at signup. organisation_profile only
    # carries a type once the applicant saves the organisation section, so it
    # can't be relied on to tell a partner application from a miner's.
    organisation_type = serializers.CharField(source="organisation.organisation_type", read_only=True)

    class Meta:
        model = ComplianceApplication
        fields = [
            "id", "reference", "organisation", "organisation_type", "status", "organisation_profile", "representative", "services",
            "professional_capability", "inspection_capability", "conflict_declaration", "declaration",
            "submitted_at", "reviewed_at", "review_notes", "conditional_requirements", "personnel",
            "documents", "conditions", "progress", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "reference", "status", "organisation_profile", "representative", "services", "professional_capability",
            "inspection_capability", "conflict_declaration", "declaration", "submitted_at", "reviewed_at",
            "review_notes", "conditional_requirements", "personnel", "documents", "conditions", "progress", "created_at", "updated_at",
        ]

    def validate_organisation(self, organisation):
        if self.instance:
            raise serializers.ValidationError("An application's organisation cannot be changed.")
        request = self.context["request"]
        if not organisation.memberships.filter(user=request.user, is_active=True).exists() and not request.user.is_staff:
            raise serializers.ValidationError("You are not an active member of this organisation.")
        return organisation

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_progress(self, obj) -> dict:
        return application_progress(obj)


class SectionSerializer(serializers.Serializer):
    data = serializers.JSONField()


class OrganisationProfileDataSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    organisation_type = serializers.ChoiceField(choices=OrganisationType.choices)
    registration_number = serializers.CharField(max_length=100)
    tax_identifier = serializers.CharField(max_length=100)
    year_established = serializers.IntegerField(min_value=1800, max_value=timezone.now().year)
    website = serializers.URLField(required=False, allow_blank=True)
    registered_address = serializers.CharField()
    operating_address = serializers.CharField()
    country = serializers.CharField(max_length=100, default="Nigeria")
    state = serializers.CharField(max_length=100)
    lga = serializers.CharField(max_length=100)


class OrganisationSectionSerializer(serializers.Serializer):
    data = OrganisationProfileDataSerializer()


class RepresentativeDataSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200)
    position = serializers.CharField(max_length=150)
    official_email = serializers.EmailField()
    official_phone = serializers.RegexField(r"^\+[1-9]\d{7,14}$", max_length=16)
    authorised = serializers.BooleanField()

    def validate_authorised(self, value):
        if not value:
            raise serializers.ValidationError("The representative must confirm their authority.")
        return value


class RepresentativeSectionSerializer(serializers.Serializer):
    data = RepresentativeDataSerializer()


class ServicesDataSerializer(serializers.Serializer):
    selected_services = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)
    geographic_coverage = serializers.ChoiceField(choices=["nationwide", "selected_states"])
    states_covered = serializers.ListField(child=serializers.CharField(max_length=100), required=False, default=list)

    def validate(self, attrs):
        if attrs["geographic_coverage"] == "selected_states" and not attrs.get("states_covered"):
            raise serializers.ValidationError({"states_covered": ["Select at least one state."]})
        return attrs


class ServicesSectionSerializer(serializers.Serializer):
    data = ServicesDataSerializer()


class ProfessionalCapabilityDataSerializer(serializers.Serializer):
    mineral_experience = serializers.ListField(child=serializers.CharField(max_length=100), max_length=100, required=False, default=list)

    def validate_mineral_experience(self, value):
        if len({item.casefold() for item in value}) != len(value):
            raise serializers.ValidationError("Mineral experience must not contain duplicates.")
        return value

    years_mining_experience = serializers.IntegerField(min_value=0)
    compliance_professionals = serializers.IntegerField(min_value=0, required=False, default=0)
    mining_engineers = serializers.IntegerField(min_value=0, required=False, default=0)
    geologists = serializers.IntegerField(min_value=0, required=False, default=0)
    environmental_specialists = serializers.IntegerField(min_value=0, required=False, default=0)
    hse_specialists = serializers.IntegerField(min_value=0, required=False, default=0)
    legal_regulatory_specialists = serializers.IntegerField(min_value=0, required=False, default=0)
    field_inspectors = serializers.IntegerField(min_value=0, required=False, default=0)
    other_technical_personnel = serializers.IntegerField(min_value=0, required=False, default=0)


class ProfessionalCapabilitySectionSerializer(serializers.Serializer):
    data = ProfessionalCapabilityDataSerializer()


class InspectionCapabilityDataSerializer(serializers.Serializer):
    conducts_physical_inspections = serializers.BooleanField()
    active_inspectors = serializers.IntegerField(min_value=0, required=False, default=0)
    maximum_inspections_per_month = serializers.IntegerField(min_value=0, required=False, default=0)
    average_turnaround_time = serializers.CharField(max_length=100, required=False, allow_blank=True)
    typical_mobilisation_time = serializers.CharField(max_length=100, required=False, allow_blank=True)
    equipment = serializers.DictField(required=False, default=dict)
    inspection_evidence_standards = serializers.ListField(child=serializers.CharField(max_length=100), required=False, default=list)


class InspectionCapabilitySectionSerializer(serializers.Serializer):
    data = InspectionCapabilityDataSerializer()


class ConflictDeclarationDataSerializer(serializers.Serializer):
    owns_assets = serializers.BooleanField()
    serves_mining_companies = serializers.BooleanField()
    trades_minerals = serializers.BooleanField()
    relationships = serializers.CharField()
    agreed = serializers.BooleanField()

    def validate_agreed(self, value):
        if not value:
            raise serializers.ValidationError("Conflict disclosure agreement is required.")
        return value


class ConflictDeclarationSectionSerializer(serializers.Serializer):
    data = ConflictDeclarationDataSerializer()


class DeclarationDataSerializer(serializers.Serializer):
    accuracy_confirmed = serializers.BooleanField()
    documents_genuine = serializers.BooleanField()
    compliance_agreed = serializers.BooleanField()
    disclose_changes = serializers.BooleanField()
    authorised = serializers.BooleanField()
    confirmed = serializers.BooleanField()
    signatory_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    signatory_position = serializers.CharField(max_length=150, required=False, allow_blank=True)
    declaration_date = serializers.DateField(required=False)

    def validate_declaration_date(self, value):
        # The section is stored in a JSONField, which cannot serialise a date
        # object; keep DateField's parsing but hand back an ISO string.
        return value.isoformat()

    def validate(self, attrs):
        confirmations = ["accuracy_confirmed", "documents_genuine", "compliance_agreed", "disclose_changes", "authorised", "confirmed"]
        rejected = [field for field in confirmations if not attrs[field]]
        if rejected:
            raise serializers.ValidationError({field: ["This declaration must be accepted."] for field in rejected})
        return attrs


class DeclarationSectionSerializer(serializers.Serializer):
    data = DeclarationDataSerializer()


class ApplicationDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[
        ApplicationStatus.UNDER_REVIEW, ApplicationStatus.ACTION_REQUIRED,
        ApplicationStatus.CONDITIONALLY_APPROVED, ApplicationStatus.VERIFIED, ApplicationStatus.REJECTED,
    ])
    notes = serializers.CharField(required=False, allow_blank=True)
    conditional_requirements = serializers.CharField(required=False, allow_blank=True)
    conditions = ApprovalConditionSerializer(many=True, required=False, allow_empty=False)

    def validate(self, attrs):
        if attrs["status"] != ApplicationStatus.CONDITIONALLY_APPROVED and "conditions" in attrs:
            raise serializers.ValidationError({"conditions": "Conditions may only accompany conditional approval."})
        return attrs


class DashboardResponseSerializer(serializers.Serializer):
    applications = serializers.ListField(child=serializers.DictField())


# Sections whose absence blocks handing an application to a reviewer. The rest
# — documents, personnel, the onboarding form — is reported as outstanding and
# left to the desk to chase through `request-document`, so an applicant can
# submit what they have rather than being stuck behind paperwork they are still
# gathering. Identity is different: an unverified applicant is not a person the
# reviewer can correspond with.
SUBMISSION_BLOCKING_SECTIONS = ("account",)


def application_progress(application):
    documents = list(application.documents.all())
    submitted_types = {doc.document_type for doc in documents if doc.file and doc.status in {"submitted", "verified"}}
    outstanding = [doc.document_type for doc in documents if not doc.file or doc.status in {"requested", "rejected"}]
    applicant = application.created_by
    if applicant is None:
        membership = application.organisation.memberships.filter(role="owner", is_active=True).select_related("user").first()
        applicant = membership.user if membership else None
    required_types = {document_type for document_type, _ in REQUIRED_DOCUMENTS}
    sections = {
        "account": bool(applicant and applicant.is_active and applicant.email_verified_at),
        "organisation": bool(application.organisation_profile and application.representative and application.services and application.professional_capability),
        "documents": required_types.issubset(submitted_types) and not outstanding,
        "personnel": application.personnel.exists(),
        "inspection_capability": bool(application.inspection_capability),
        "conflict_declaration": bool(application.conflict_declaration),
        "declaration": bool(application.declaration.get("confirmed")),
    }
    # A document the desk has looked at and rejected is not the same as one that
    # has not arrived yet. "Still gathering it" is fine to submit alongside;
    # "I read it and it is wrong" has to be answered, or the applicant can hand
    # the same file straight back and the review goes round again.
    rejected = [doc.document_type for doc in documents if doc.status == ComplianceDocument.Status.REJECTED]

    completed = sum(sections.values())
    return {
        "percent": round(completed / len(sections) * 100), "completed": completed, "total": len(sections),
        "sections": sections,
        # What is still missing, and the subset of that which actually prevents
        # a submission. The frontend shows the first and gates on the second.
        "outstanding_sections": [name for name, done in sections.items() if not done],
        "blocking": (
            [name for name in SUBMISSION_BLOCKING_SECTIONS if not sections[name]]
            + (["rejected_documents"] if rejected else [])
        ),
        "documents": {
            "submitted": len(required_types & submitted_types), "required": len(required_types),
            "outstanding": outstanding, "rejected": rejected,
        },
    }
