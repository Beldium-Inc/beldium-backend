from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from quality import models as m

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip",
}


def validate_upload(value):
    if value.size > MAX_UPLOAD_SIZE:
        raise serializers.ValidationError("File too large; the maximum size is 10 MB.")
    if getattr(value, "content_type", None) not in ALLOWED_CONTENT_TYPES:
        raise serializers.ValidationError("Only PDF, Word, Excel, ZIP, JPEG, and PNG files are supported.")
    return value


class QualityApplicationSerializer(serializers.ModelSerializer):
    organisation = serializers.JSONField(source="organisation_data")
    capability = serializers.JSONField(source="capability_data")
    laboratory = serializers.JSONField(source="laboratory_data")
    assigned_to = serializers.SerializerMethodField()

    class Meta:
        model = m.QualityApplication
        fields = [
            "id", "reference", "submitted_at", "status", "assigned_to", "risk_score",
            "organisation", "capability", "laboratory", "documents", "risk_flags",
            "audit", "decision_note", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]

    def get_assigned_to(self, obj):
        if obj.assigned_to_id:
            return getattr(obj.assigned_to, "full_name", "") or obj.assigned_to.email
        return ""


class QualityApplicationCreateSerializer(serializers.ModelSerializer):
    organisation = serializers.JSONField(source="organisation_data", required=False, default=dict)
    capability = serializers.JSONField(source="capability_data", required=False, default=dict)
    laboratory = serializers.JSONField(source="laboratory_data", required=False, default=dict)

    class Meta:
        model = m.QualityApplication
        fields = ["organisation", "capability", "laboratory"]


class PartnerDocumentSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
    issuer = serializers.CharField(required=False, allow_blank=True)
    issued = serializers.CharField(required=False, allow_blank=True)
    expires = serializers.CharField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=m.DocStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True)
    conditional_on = serializers.CharField(required=False, allow_blank=True)


class RiskFlagSerializer(serializers.Serializer):
    id = serializers.CharField()
    severity = serializers.CharField(required=False)
    title = serializers.CharField(required=False, allow_blank=True)
    detail = serializers.CharField(required=False, allow_blank=True)
    resolved = serializers.BooleanField(required=False)


class DecideApplicationSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.ApplicationStatus.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class SetDocumentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.DocStatus.choices)


# --- samples -----------------------------------------------------------------

class SampleSerializer(serializers.ModelSerializer):
    buyer_spec = serializers.PrimaryKeyRelatedField(queryset=m.BuyerSpec.objects.all(), allow_null=True, required=False)
    miner_organisation = serializers.PrimaryKeyRelatedField(read_only=True)
    partner_organisation = serializers.PrimaryKeyRelatedField(read_only=True)
    buyer_organisation = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        ref_name = "QualitySample"
        model = m.Sample
        fields = [
            "id", "reference", "material", "lot", "mine_site", "origin", "mass_kg",
            "registered_at", "miner_org", "partner_org", "buyer_org", "buyer_spec",
            "miner_organisation", "partner_organisation", "buyer_organisation",
            "status", "custody", "test_request", "results", "quality_review", "audit",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "registered_at", "created_at", "updated_at", "custody", "test_request", "results", "quality_review", "audit"]


class NewSampleSerializer(serializers.Serializer):
    material = serializers.CharField()
    lot = serializers.CharField(required=False, allow_blank=True, default="")
    mine_site = serializers.CharField(required=False, allow_blank=True, default="")
    origin = serializers.CharField(required=False, allow_blank=True, default="")
    mass_kg = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0)
    buyer_spec = serializers.PrimaryKeyRelatedField(queryset=m.BuyerSpec.objects.all(), required=False, allow_null=True)


class SampleStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=m.SampleStatus.choices)


class CustodyEventInputSerializer(serializers.Serializer):
    action = serializers.CharField()
    location = serializers.CharField(required=False, allow_blank=True, default="")
    seal_intact = serializers.BooleanField(required=False, default=True)


class TestRequestInputSerializer(serializers.Serializer):
    methods = serializers.ListField(child=serializers.CharField())
    priority = serializers.ChoiceField(choices=["standard", "expedited"])
    turnaround = serializers.CharField(required=False, allow_blank=True, default="")


class ResultVerdictInputSerializer(serializers.Serializer):
    value = serializers.CharField(required=False, allow_blank=True)
    unit = serializers.CharField(required=False, allow_blank=True)
    spec = serializers.CharField(required=False, allow_blank=True)
    uncertainty = serializers.CharField(required=False, allow_blank=True)


class TestResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    analyte = serializers.CharField(required=False, allow_blank=True)
    method = serializers.CharField(required=False, allow_blank=True)
    value = serializers.CharField(required=False, allow_blank=True)
    unit = serializers.CharField(required=False, allow_blank=True)
    spec = serializers.CharField(required=False, allow_blank=True)
    verdict = serializers.ChoiceField(choices=m.ResultVerdict.choices)
    uncertainty = serializers.CharField(required=False, allow_blank=True)


class QualityReviewInputSerializer(serializers.Serializer):
    verdict = serializers.ChoiceField(choices=m.ResultVerdict.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


# --- buyer specs ---------------------------------------------------------------

class BuyerSpecSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.BuyerSpec
        fields = ["id", "name", "buyer_org", "buyer_organisation", "material", "limits"]
        read_only_fields = ["id"]


# --- certificates --------------------------------------------------------------

class CertificateSerializer(serializers.ModelSerializer):
    sample_reference = serializers.CharField(source="sample.reference", read_only=True)
    material = serializers.CharField(source="sample.material", read_only=True)

    class Meta:
        model = m.Certificate
        fields = [
            "id", "reference", "sample", "sample_reference", "material", "issued_at",
            "issued_by", "valid_until", "status", "verification_hash", "scans",
        ]
        read_only_fields = ["id", "reference", "issued_at", "verification_hash", "scans"]


class CertificateVerificationSerializer(serializers.ModelSerializer):
    sample_reference = serializers.CharField(source="sample.reference", read_only=True)
    material = serializers.CharField(source="sample.material", read_only=True)

    class Meta:
        model = m.Certificate
        fields = ["reference", "sample_reference", "material", "issued_at", "valid_until", "status", "scans"]


class QualityApplicationDocumentSerializer(serializers.ModelSerializer):
    original_name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = m.QualityApplicationDocument
        fields = ["id", "application", "document_id", "name", "category", "file", "file_url", "original_name", "created_at"]
        read_only_fields = ["id", "application", "file_url", "original_name", "created_at"]
        extra_kwargs = {"file": {"write_only": True}}

    def validate_file(self, value):
        return validate_upload(value)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj):
        if not obj.file:
            return None
        url = reverse("quality-application-document-download", args=[obj.application_id, obj.document_id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


# --- non-conformities -------------------------------------------------------------

class CorrectiveActionSerializer(serializers.Serializer):
    id = serializers.CharField()
    action = serializers.CharField(required=False, allow_blank=True)
    owner = serializers.CharField(required=False, allow_blank=True)
    due = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=m.CAPAStatus.choices)


class QualityNonConformitySerializer(serializers.ModelSerializer):
    class Meta:
        model = m.QualityNonConformity
        fields = [
            "id", "reference", "title", "raised_at", "raised_by", "against",
            "severity", "status", "detail", "capa",
        ]
        read_only_fields = ["id", "reference", "raised_at", "status", "capa"]


class RaiseNonConformitySerializer(serializers.Serializer):
    title = serializers.CharField()
    against = serializers.CharField(required=False, allow_blank=True, default="")
    severity = serializers.ChoiceField(choices=m.NCSeverity.choices)
    detail = serializers.CharField(required=False, allow_blank=True, default="")


class AddCorrectiveActionSerializer(serializers.Serializer):
    action = serializers.CharField()
    owner = serializers.CharField(required=False, allow_blank=True, default="")
    due = serializers.CharField(required=False, allow_blank=True, default="")


# --- capabilities / dashboard --------------------------------------------------

class QualityCapabilitiesSerializer(serializers.Serializer):
    role = serializers.CharField(allow_null=True)
    can_review = serializers.BooleanField()
    can_decide = serializers.BooleanField()
    is_staff = serializers.BooleanField()


class QualityNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.QualityNotification
        fields = ["id", "title", "body", "event", "read_at", "created_at", "sample", "application", "certificate", "non_conformity"]
        read_only_fields = fields


# --- professional applications ------------------------------------------------

# (document_type, title, required)
PROFESSIONAL_DOCUMENTS = (
    ("professional_qualifications", "Professional Qualifications", True),
    ("inspection_credentials", "Inspection Credentials", True),
    ("sampling_credentials", "Sampling Credentials", False),
    ("professional_certifications", "Professional Certifications", False),
    ("other_supporting_documents", "Other Supporting Documents", False),
)


class _JSONDateField(serializers.DateField):
    """A date that ends up in a JSONField, so it is handed on as an ISO string."""

    def to_internal_value(self, value):
        return super().to_internal_value(value).isoformat()


class ProfessionalPersonalSerializer(serializers.Serializer):
    full_legal_name = serializers.CharField(max_length=200)
    date_of_birth = _JSONDateField()
    national_id = serializers.CharField(max_length=50)
    job_title = serializers.CharField(max_length=150)
    base_city = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")


class ProfessionalQualificationSerializer(serializers.Serializer):
    qualification = serializers.CharField(max_length=255)
    institution = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    year = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class ProfessionalCertificationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    certificate_number = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    expiry = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class ProfessionalCapabilitySerializer(serializers.Serializer):
    capabilities = serializers.ListField(child=serializers.CharField(max_length=150), allow_empty=False)
    minerals = serializers.ListField(child=serializers.CharField(max_length=150), allow_empty=False)


class ProfessionalExperienceSerializer(serializers.Serializer):
    years_experience = serializers.IntegerField(min_value=0, max_value=80)
    previous_employer = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    summary = serializers.CharField()


class ProfessionalDeclarationSerializer(serializers.Serializer):
    information_true = serializers.BooleanField()
    consent_to_verification = serializers.BooleanField()
    understands_verification = serializers.BooleanField()
    signature = serializers.CharField(max_length=200)

    def validate(self, attrs):
        confirmations = ["information_true", "consent_to_verification", "understands_verification"]
        rejected = [field for field in confirmations if not attrs[field]]
        if rejected:
            raise serializers.ValidationError({field: ["This declaration must be accepted."] for field in rejected})
        return attrs


class ProfessionalApplicationInputSerializer(serializers.Serializer):
    organisation = serializers.UUIDField()
    role = serializers.ChoiceField(choices=["officer_inspector"], default="officer_inspector")
    personal = ProfessionalPersonalSerializer()
    qualifications = ProfessionalQualificationSerializer(many=True, required=False, default=list)
    certifications = ProfessionalCertificationSerializer(many=True, required=False, default=list)
    capability = ProfessionalCapabilitySerializer()
    experience = ProfessionalExperienceSerializer()
    declaration = ProfessionalDeclarationSerializer()


class QualityProfessionalDocumentSerializer(serializers.ModelSerializer):
    original_name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = m.QualityProfessionalDocument
        fields = ["id", "document_type", "title", "original_name", "file_url", "created_at"]

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj):
        url = reverse("quality-professional-application-document-download", args=[obj.application_id, obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class QualityProfessionalApplicationSerializer(serializers.ModelSerializer):
    documents = QualityProfessionalDocumentSerializer(many=True, read_only=True)
    applicant_email = serializers.EmailField(source="applicant.email", read_only=True)
    organisation_name = serializers.CharField(source="organisation.name", read_only=True, default="")

    class Meta:
        model = m.QualityProfessionalApplication
        fields = [
            "id", "reference", "status", "role", "applicant_email", "organisation", "organisation_name",
            "personal", "qualifications", "certifications", "capability", "experience", "declaration",
            "documents", "audit", "decision_note", "submitted_at", "created_at", "updated_at",
        ]
        read_only_fields = fields
