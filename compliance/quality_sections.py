"""The Quality & Control organisation application.

A Q&C applicant is asked about laboratories, accreditation, methods, equipment
and sampling rather than the shared seven sections, so its answers live in
``ComplianceApplication.quality_profile`` under the keys below, each written
through its own ``sections/quality-<key>/`` route. An application counts as a
Q&C one as soon as that profile holds anything.
"""
from rest_framework import serializers

from organisations.models import OrganisationType


# The organisation types a Q&C applicant picks from, and the platform-wide
# type each one registers as (which is what decides the desk it lands on).
QUALITY_ORGANISATION_TYPES = {
    "Laboratory": OrganisationType.LABORATORY,
    "Assay Organisation": OrganisationType.LABORATORY,
    "Inspection Organisation": OrganisationType.INSPECTION_BODY,
    "Quality Assurance Organisation": OrganisationType.COMPLIANCE_PARTNER,
    "Conformity Assessment Organisation": OrganisationType.COMPLIANCE_PARTNER,
    "Sampling Organisation": OrganisationType.INSPECTION_BODY,
    "Mineral Testing Organisation": OrganisationType.LABORATORY,
    "Independent Inspection Organisation": OrganisationType.INSPECTION_BODY,
    "Regulatory / Standards Institution": OrganisationType.REGULATOR,
    "Other": OrganisationType.COMPLIANCE_PARTNER,
}

# (document_type, title, required)
QUALITY_DOCUMENTS = (
    ("qc_organisation_registration", "CAC / Organisation Registration", True),
    ("qc_tin", "TIN", True),
    ("qc_laboratory_registration", "Laboratory Registration", True),
    ("qc_accreditation_certificate", "Accreditation Certificate", True),
    ("qc_accreditation_scope", "Accreditation Scope", True),
    ("qc_testing_method_evidence", "Testing Method Evidence", False),
    ("qc_equipment_calibration_certificates", "Equipment Calibration Certificates", False),
    ("qc_quality_management_documents", "Quality Management Documents", False),
    ("qc_regulatory_approvals", "Relevant Regulatory Approvals", False),
    ("qc_insurance", "Insurance", False),
    ("qc_other_supporting_documents", "Other Supporting Documents", False),
)
QUALITY_DOCUMENT_TITLES = {key: title for key, title, _ in QUALITY_DOCUMENTS}
QUALITY_REQUIRED_DOCUMENT_TYPES = {key for key, _, required in QUALITY_DOCUMENTS if required}

# Profile keys an application has to hold before it reads as complete.
QUALITY_REQUIRED_SECTIONS = ("organisation", "services", "accreditation", "testing-methods", "sampling")


def is_quality_application(application):
    return bool(application.quality_profile)


def _words(max_length=150):
    return serializers.ListField(child=serializers.CharField(max_length=max_length), allow_empty=False)


class QualityOrganisationDataSerializer(serializers.Serializer):
    legal_name = serializers.CharField(max_length=255)
    trading_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    organisation_type = serializers.ChoiceField(choices=list(QUALITY_ORGANISATION_TYPES))
    registration_number = serializers.CharField(max_length=100)
    tax_identifier = serializers.CharField(max_length=100)
    registered_address = serializers.CharField()
    country = serializers.CharField(max_length=100)
    website = serializers.URLField(required=False, allow_blank=True, default="")


class QualityServicesDataSerializer(serializers.Serializer):
    capabilities = _words()
    minerals = _words()


class QualityLaboratorySerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    location = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    registration_number = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class QualityLaboratoriesDataSerializer(serializers.Serializer):
    laboratories = QualityLaboratorySerializer(many=True, required=False, default=list)


class QualityAccreditationDataSerializer(serializers.Serializer):
    accreditation_body = serializers.CharField(max_length=255)
    accreditation_number = serializers.CharField(max_length=150)
    accreditation_expiry = serializers.DateField()
    standard = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    accredited_scope = serializers.CharField()

    def validate_accreditation_expiry(self, value):
        # The profile is a JSONField, which cannot hold a date object.
        return value.isoformat()


class QualityTestingMethodsDataSerializer(serializers.Serializer):
    testing_methods = _words()


class QualityEquipmentSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    serial_number = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    calibration_date = serializers.DateField(required=False, allow_null=True, default=None)

    def to_internal_value(self, data):
        # A row with no date arrives as "", which DateField would reject.
        if isinstance(data, dict) and data.get("calibration_date") == "":
            data = {**data, "calibration_date": None}
        return super().to_internal_value(data)

    def validate_calibration_date(self, value):
        return value.isoformat() if value else ""


class QualityEquipmentDataSerializer(serializers.Serializer):
    equipment = QualityEquipmentSerializer(many=True, required=False, default=list)


class QualityKeyPersonSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200)
    role = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")


class QualityPersonnelDataSerializer(serializers.Serializer):
    personnel = QualityKeyPersonSerializer(many=True, required=False, default=list)


class QualitySamplingDataSerializer(serializers.Serializer):
    geographic_coverage = _words()
    field_sampling_teams = serializers.IntegerField(min_value=0)
    tamper_evident_sealing = serializers.ChoiceField(choices=["Numbered seals", "Numbered seals + photo", "None"])
    sampling_procedure_summary = serializers.CharField(required=False, allow_blank=True, default="")


class QualityDeclarationDataSerializer(serializers.Serializer):
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


# Route slug (after "quality-") -> the serializer that validates its `data`.
QUALITY_SECTIONS = {
    "organisation": QualityOrganisationDataSerializer,
    "services": QualityServicesDataSerializer,
    "laboratories": QualityLaboratoriesDataSerializer,
    "accreditation": QualityAccreditationDataSerializer,
    "testing-methods": QualityTestingMethodsDataSerializer,
    "equipment": QualityEquipmentDataSerializer,
    "personnel": QualityPersonnelDataSerializer,
    "sampling": QualitySamplingDataSerializer,
    "declaration": QualityDeclarationDataSerializer,
}


def quality_section_serializer(key):
    """The `{data: ...}` envelope every section route takes, for one section."""
    return type(
        f"Quality{key.title().replace('-', '')}SectionSerializer",
        (serializers.Serializer,),
        {"data": QUALITY_SECTIONS[key]()},
    )
