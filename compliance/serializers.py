from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field

from compliance.models import ApplicationMessage, ApplicationStatus, ComplianceApplication, ComplianceDocument, Personnel, REQUIRED_DOCUMENTS
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

    def _url(self, file):
        if not file:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(file.url) if request else file.url

    @extend_schema_field(OpenApiTypes.URI)
    def get_cv_url(self, obj) -> str | None:
        return self._url(obj.cv)

    @extend_schema_field(OpenApiTypes.URI)
    def get_certificate_url(self, obj) -> str | None:
        return self._url(obj.certificate)


class ComplianceDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    original_name = serializers.CharField(read_only=True)

    class Meta:
        model = ComplianceDocument
        fields = ["id", "document_type", "title", "file", "file_url", "original_name", "status", "request_message", "review_notes", "reviewed_at", "created_at", "updated_at"]
        read_only_fields = ["id", "file_url", "original_name", "status", "request_message", "review_notes", "reviewed_at", "created_at", "updated_at"]
        extra_kwargs = {"file": {"write_only": True, "required": True}}

    def validate_file(self, value):
        return validate_upload(value)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.file.url) if request else obj.file.url


class RequestedDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceDocument
        fields = ["id", "document_type", "title", "request_message", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


class DocumentReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[ComplianceDocument.Status.VERIFIED, ComplianceDocument.Status.REJECTED])
    notes = serializers.CharField(required=False, allow_blank=True)


class ApplicationMessageSerializer(serializers.ModelSerializer):
    author_email = serializers.EmailField(source="author.email", read_only=True)

    class Meta:
        model = ApplicationMessage
        fields = ["id", "author_email", "body", "is_internal", "read_at", "created_at"]
        read_only_fields = ["id", "author_email", "is_internal", "read_at", "created_at"]


class ComplianceApplicationSerializer(serializers.ModelSerializer):
    personnel = PersonnelSerializer(many=True, read_only=True)
    documents = ComplianceDocumentSerializer(many=True, read_only=True)
    progress = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceApplication
        fields = [
            "id", "organisation", "status", "organisation_profile", "representative", "services",
            "professional_capability", "inspection_capability", "conflict_declaration", "declaration",
            "submitted_at", "reviewed_at", "review_notes", "conditional_requirements", "personnel",
            "documents", "progress", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "organisation_profile", "representative", "services", "professional_capability",
            "inspection_capability", "conflict_declaration", "declaration", "submitted_at", "reviewed_at",
            "review_notes", "conditional_requirements", "personnel", "documents", "progress", "created_at", "updated_at",
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


class DashboardResponseSerializer(serializers.Serializer):
    applications = serializers.ListField(child=serializers.DictField())


def application_progress(application):
    submitted_types = set(application.documents.exclude(file="").values_list("document_type", flat=True))
    required_types = {document_type for document_type, _ in REQUIRED_DOCUMENTS}
    sections = {
        "account": bool(application.organisation_id),
        "organisation": bool(application.organisation_profile and application.representative and application.services and application.professional_capability),
        "documents": required_types.issubset(submitted_types),
        "personnel": application.personnel.exists(),
        "inspection_capability": bool(application.inspection_capability),
        "conflict_declaration": bool(application.conflict_declaration),
        "declaration": bool(application.declaration.get("confirmed")),
    }
    completed = sum(sections.values())
    return {
        "percent": round(completed / len(sections) * 100), "completed": completed, "total": len(sections),
        "sections": sections, "documents": {"submitted": len(required_types & submitted_types), "required": len(required_types)},
    }
