"""DRF serialisers for the Processing Compliance register.

Field names are the model's, so the frontend's generated types stay a faithful
mirror. Anything computed (risk band, document validity, yield) is exposed as a
read-only field rather than being recomputed in the client, so every consumer
of the API agrees on the number.
"""
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import AccountAuditEvent
from processing import scoring
from processing.models import (
    ApplicationSection,
    ComplianceReport,
    EnvironmentalAlert,
    Facility,
    Incident,
    Inspection,
    NonConformity,
    NonConformityEvidence,
    ProcessingApplication,
    ProcessingDocument,
    Processor,
    RiskCause,
    SectionKey,
    TraceabilityRun,
)

MAX_UPLOAD_SIZE = 10 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/jpeg",
    "image/png",
}


def validate_upload(value):
    if value.size > MAX_UPLOAD_SIZE:
        raise serializers.ValidationError("File too large; the maximum size is 10 MB.")
    if getattr(value, "content_type", None) not in ALLOWED_CONTENT_TYPES:
        raise serializers.ValidationError("Only PDF, Word, JPEG, and PNG files are supported.")
    return value


def actor_name(user):
    if user is None:
        return "System"
    return user.full_name or user.email


# --- documents ---------------------------------------------------------------


class ProcessingDocumentSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)
    days_to_expiry = serializers.IntegerField(read_only=True)
    original_name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ProcessingDocument
        fields = [
            "id",
            "application",
            "processor",
            "section",
            "name",
            "reference",
            "issuer",
            "issued_on",
            "expires_on",
            "status",
            "days_to_expiry",
            "review_state",
            "review_note",
            "reviewed_at",
            "file",
            "file_url",
            "original_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "status", "days_to_expiry", "review_state", "review_note", "reviewed_at",
            "file_url", "original_name", "created_at", "updated_at",
        ]
        extra_kwargs = {"file": {"write_only": True, "required": False}}

    def validate_file(self, value):
        return validate_upload(value)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        # Point at the authenticated download route, never at storage: a local
        # media path has nothing guarding it.
        if not obj.file:
            return None
        url = reverse("processing-document-download", args=[obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ExpiringDocumentSerializer(serializers.ModelSerializer):
    """Register-wide expiry watchlist row."""

    status = serializers.CharField(read_only=True)
    days_to_expiry = serializers.IntegerField(read_only=True)
    company = serializers.SerializerMethodField()

    class Meta:
        model = ProcessingDocument
        fields = ["id", "name", "reference", "company", "issuer", "expires_on", "days_to_expiry", "status"]

    def get_company(self, obj) -> str:
        if obj.processor_id:
            return obj.processor.name
        return obj.application.company if obj.application_id else ""


# --- applications ------------------------------------------------------------


class ApplicationSectionSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ApplicationSection
        fields = [
            "id",
            "key",
            "label",
            "fields",
            "notes",
            "review_state",
            "review_note",
            "reviewed_by_name",
            "reviewed_at",
            "documents",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "label", "review_state", "review_note", "reviewed_by_name", "reviewed_at", "documents", "created_at", "updated_at"]

    def get_label(self, obj) -> str:
        return SectionKey(obj.key).label

    def get_reviewed_by_name(self, obj) -> str | None:
        return actor_name(obj.reviewed_by) if obj.reviewed_by_id else None

    @extend_schema_field(ProcessingDocumentSerializer(many=True))
    def get_documents(self, obj):
        # Prefetched on the application, so this filters in memory rather than
        # issuing one query per section.
        documents = [d for d in obj.application.documents.all() if d.section == obj.key]
        return ProcessingDocumentSerializer(documents, many=True, context=self.context).data

    def validate_fields(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Section fields must be a list.")
        for entry in value:
            if not isinstance(entry, dict) or "label" not in entry:
                raise serializers.ValidationError("Each field needs at least a label.")
            if set(entry) - {"label", "value", "flag"}:
                raise serializers.ValidationError("A field accepts only label, value and flag.")
            if entry.get("flag") not in {None, "ok", "warn", "bad"}:
                raise serializers.ValidationError("flag must be one of ok, warn, bad.")
        return value


class RiskCauseSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskCause
        fields = ["id", "cause", "weight", "detail", "created_at"]
        read_only_fields = ["id", "created_at"]


class ProcessingApplicationSerializer(serializers.ModelSerializer):
    risk_score = serializers.SerializerMethodField()
    risk_band = serializers.SerializerMethodField()
    completeness = serializers.SerializerMethodField()
    processing_type_label = serializers.SerializerMethodField()
    risk_causes = RiskCauseSerializer(many=True, read_only=True)
    open_non_conformities = serializers.SerializerMethodField()

    class Meta:
        model = ProcessingApplication
        fields = [
            "id",
            "reference",
            "processor",
            "organisation",
            "company",
            "rc_number",
            "tin",
            "processing_type",
            "processing_type_label",
            "state",
            "lga",
            "facility_name",
            "capacity",
            "workforce",
            "contact_name",
            "contact_email",
            "contact_phone",
            "stage",
            "decision",
            "decision_note",
            "decided_at",
            "submitted_on",
            "risk_score",
            "risk_band",
            "completeness",
            "open_non_conformities",
            "risk_causes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "reference",
            "stage",
            "decision",
            "decision_note",
            "decided_at",
            "risk_score",
            "risk_band",
            "completeness",
            "open_non_conformities",
            "risk_causes",
            "processing_type_label",
            "created_at",
            "updated_at",
        ]

    def get_risk_score(self, obj) -> int:
        return scoring.risk_score(obj)

    def get_risk_band(self, obj) -> str:
        return scoring.risk_band(scoring.risk_score(obj))

    def get_completeness(self, obj) -> int:
        return scoring.completeness(obj)

    def get_processing_type_label(self, obj) -> str:
        return obj.get_processing_type_display()

    def get_open_non_conformities(self, obj) -> int:
        return sum(1 for nc in obj.non_conformities.all() if nc.status != NonConformity.Status.CLOSED)


class ProcessingApplicationDetailSerializer(ProcessingApplicationSerializer):
    sections = ApplicationSectionSerializer(many=True, read_only=True)
    review = serializers.SerializerMethodField()

    class Meta(ProcessingApplicationSerializer.Meta):
        fields = ProcessingApplicationSerializer.Meta.fields + ["sections", "review"]
        read_only_fields = ProcessingApplicationSerializer.Meta.read_only_fields + ["sections", "review"]

    def get_review(self, obj) -> dict:
        return scoring.review_summary(obj)


class ProcessingSectionReviewSerializer(serializers.Serializer):
    review_state = serializers.ChoiceField(choices=["verified", "rejected", "info_requested", "flagged", "pending"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ProcessingDecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approved", "conditional_approval", "more_info_required", "rejected"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class InspectionRequestSerializer(serializers.Serializer):
    scheduled_for = serializers.DateField(required=False, allow_null=True)
    inspection_type = serializers.ChoiceField(choices=Inspection.Type.choices, default=Inspection.Type.PRE_APPROVAL)
    inspector_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate_scheduled_for(self, value):
        if value and value < timezone.localdate():
            raise serializers.ValidationError("An inspection cannot be scheduled in the past.")
        return value


# --- processors --------------------------------------------------------------


class FacilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Facility
        fields = ["id", "processor", "name", "address", "state", "lga", "capacity", "workforce", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class ProcessorSerializer(serializers.ModelSerializer):
    processing_type_label = serializers.SerializerMethodField()
    facilities_count = serializers.IntegerField(read_only=True)
    open_non_conformities = serializers.IntegerField(read_only=True)

    class Meta:
        model = Processor
        fields = [
            "id",
            "reference",
            "organisation",
            "name",
            "rc_number",
            "tin",
            "processing_type",
            "processing_type_label",
            "country",
            "state",
            "lga",
            "region",
            "status",
            "compliance_score",
            "registered_on",
            "last_inspection_on",
            "facilities_count",
            "open_non_conformities",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "processing_type_label", "facilities_count", "open_non_conformities", "created_at", "updated_at"]

    def get_processing_type_label(self, obj) -> str:
        return obj.get_processing_type_display()


class ProcessorDetailSerializer(ProcessorSerializer):
    facilities = FacilitySerializer(many=True, read_only=True)

    class Meta(ProcessorSerializer.Meta):
        fields = ProcessorSerializer.Meta.fields + ["facilities"]
        read_only_fields = ProcessorSerializer.Meta.read_only_fields + ["facilities"]


# --- findings ----------------------------------------------------------------


class ProcessingDocumentReviewSerializer(serializers.Serializer):
    review_state = serializers.ChoiceField(choices=["verified", "rejected", "pending"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class NonConformityEvidenceSerializer(serializers.ModelSerializer):
    submitted_by_name = serializers.SerializerMethodField()
    original_name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = NonConformityEvidence
        fields = ["id", "name", "note", "file", "file_url", "original_name", "submitted_by_name", "created_at"]
        read_only_fields = ["id", "file_url", "original_name", "submitted_by_name", "created_at"]
        extra_kwargs = {"file": {"write_only": True, "required": False}}

    def validate_file(self, value):
        return validate_upload(value)

    def get_submitted_by_name(self, obj) -> str:
        return actor_name(obj.submitted_by)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        url = reverse("processing-non-conformity-download-evidence", args=[obj.non_conformity_id, obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class NonConformitySerializer(serializers.ModelSerializer):
    section_label = serializers.SerializerMethodField()
    company = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    evidence = NonConformityEvidenceSerializer(many=True, read_only=True)

    class Meta:
        model = NonConformity
        fields = [
            "id",
            "reference",
            "application",
            "processor",
            "company",
            "section",
            "section_label",
            "severity",
            "title",
            "detail",
            "raised_on",
            "due_on",
            "status",
            "is_overdue",
            "closure_note",
            "closed_at",
            "evidence",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "company", "section_label", "status", "is_overdue", "closure_note", "closed_at", "evidence", "raised_on", "created_at", "updated_at"]

    def get_section_label(self, obj) -> str:
        return SectionKey(obj.section).label

    def get_company(self, obj) -> str:
        if obj.application_id:
            return obj.application.company
        return obj.processor.name if obj.processor_id else ""

    def validate_due_on(self, value):
        if value < timezone.localdate():
            raise serializers.ValidationError("The corrective-action deadline cannot be in the past.")
        return value

    def validate(self, attrs):
        if not attrs.get("application") and not attrs.get("processor"):
            raise serializers.ValidationError("A finding must name an application or a processor.")
        return attrs


class NonConformityClosureSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


# --- operations --------------------------------------------------------------


class InspectionSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()
    inspector_display = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = [
            "id",
            "reference",
            "application",
            "processor",
            "company",
            "facility",
            "facility_name",
            "state",
            "scheduled_for",
            "inspector",
            "inspector_name",
            "inspector_display",
            "inspection_type",
            "status",
            "outcome",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "company", "inspector_display", "completed_at", "created_at", "updated_at"]

    def get_company(self, obj) -> str:
        if obj.application_id:
            return obj.application.company
        return obj.processor.name if obj.processor_id else ""

    def get_inspector_display(self, obj) -> str:
        if obj.inspector_id:
            return actor_name(obj.inspector)
        return obj.inspector_name or "Unassigned"


class EnvironmentalAlertSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()

    class Meta:
        model = EnvironmentalAlert
        fields = [
            "id",
            "reference",
            "processor",
            "company",
            "facility",
            "facility_name",
            "state",
            "parameter",
            "reading",
            "threshold",
            "severity",
            "detected_on",
            "status",
            "acknowledged_at",
            "resolved_at",
            "resolution_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "company", "status", "acknowledged_at", "resolved_at", "created_at", "updated_at"]

    def get_company(self, obj) -> str:
        return obj.processor.name if obj.processor_id else ""


class AlertStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["acknowledged", "resolved"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class IncidentSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()

    class Meta:
        model = Incident
        fields = [
            "id",
            "reference",
            "processor",
            "company",
            "facility",
            "facility_name",
            "state",
            "incident_type",
            "severity",
            "reported_on",
            "status",
            "summary",
            "closed_at",
            "closure_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "company", "closed_at", "created_at", "updated_at"]

    def get_company(self, obj) -> str:
        return obj.processor.name if obj.processor_id else ""


class TraceabilityRunSerializer(serializers.ModelSerializer):
    company = serializers.SerializerMethodField()
    yield_percent = serializers.FloatField(read_only=True)

    class Meta:
        model = TraceabilityRun
        fields = [
            "id",
            "reference",
            "processor",
            "company",
            "facility",
            "facility_name",
            "input_batch",
            "input_source",
            "input_mass_kg",
            "process",
            "started_at",
            "completed_at",
            "output_batch",
            "output_mass_kg",
            "yield_percent",
            "qc_assay",
            "qc_moisture",
            "qc_verdict",
            "qc_lab",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "company", "yield_percent", "created_at", "updated_at"]

    def get_company(self, obj) -> str:
        return obj.processor.name if obj.processor_id else ""

    def validate(self, attrs):
        instance = self.instance
        input_mass = attrs.get("input_mass_kg", getattr(instance, "input_mass_kg", 0))
        output_mass = attrs.get("output_mass_kg", getattr(instance, "output_mass_kg", 0))
        if output_mass > input_mass:
            raise serializers.ValidationError(
                {"output_mass_kg": "Output mass cannot exceed the input mass of the run."}
            )
        started = attrs.get("started_at", getattr(instance, "started_at", None))
        completed = attrs.get("completed_at", getattr(instance, "completed_at", None))
        if started and completed and completed < started:
            raise serializers.ValidationError({"completed_at": "A run cannot finish before it starts."})
        return attrs


class ComplianceReportSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = ComplianceReport
        fields = ["id", "reference", "title", "period_label", "scope", "generated_on", "pages", "file_url", "created_at"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        url = reverse("processing-report-download", args=[obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


# --- audit -------------------------------------------------------------------


class ProcessingAuditEventSerializer(serializers.ModelSerializer):
    """One line of the processing audit trail.

    The actor's email, IP and user agent stay out: this feed is read across
    organisations, and an operator's network detail is nobody else's business.
    """

    actor = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    action = serializers.SerializerMethodField()
    target = serializers.SerializerMethodField()
    detail = serializers.SerializerMethodField()

    class Meta:
        model = AccountAuditEvent
        fields = ["id", "created_at", "actor", "role", "action", "target", "detail"]

    def get_actor(self, obj) -> str:
        return actor_name(obj.actor)

    def get_role(self, obj) -> str:
        if obj.actor is None:
            return "System"
        return "Compliance Operator" if obj.actor.is_staff else (obj.metadata or {}).get("role", "Platform user")

    def get_action(self, obj) -> str:
        return obj.event_type.removeprefix("processing.").replace("_", " ").capitalize()

    def get_target(self, obj) -> str:
        return (obj.metadata or {}).get("target", "")

    def get_detail(self, obj) -> str:
        return (obj.metadata or {}).get("detail", "")


# --- dashboard ---------------------------------------------------------------


class RegionalComplianceSerializer(serializers.Serializer):
    region = serializers.CharField()
    processors = serializers.IntegerField()
    compliant = serializers.IntegerField()
    conditional = serializers.IntegerField()
    suspended = serializers.IntegerField()
    avg_score = serializers.IntegerField()


class KpiPointSerializer(serializers.Serializer):
    month = serializers.CharField()
    approvals = serializers.IntegerField()
    nonconformities = serializers.IntegerField()
    inspections = serializers.IntegerField()


class NotificationSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    at = serializers.DateTimeField()
    kind = serializers.ChoiceField(choices=["info", "warn", "error"])


class DashboardTotalsSerializer(serializers.Serializer):
    applications = serializers.IntegerField()
    applications_in_review = serializers.IntegerField()
    applications_awaiting_info = serializers.IntegerField()
    processors = serializers.IntegerField()
    approved_processors = serializers.IntegerField()
    suspended_processors = serializers.IntegerField()
    open_non_conformities = serializers.IntegerField()
    overdue_non_conformities = serializers.IntegerField()
    upcoming_inspections = serializers.IntegerField()
    open_environmental_alerts = serializers.IntegerField()
    open_incidents = serializers.IntegerField()
    expiring_documents = serializers.IntegerField()
    average_compliance_score = serializers.IntegerField()


class DashboardSerializer(serializers.Serializer):
    audience = serializers.CharField(allow_null=True)
    capabilities = serializers.DictField()
    totals = DashboardTotalsSerializer()
    kpi_trend = KpiPointSerializer(many=True)
    regional_compliance = RegionalComplianceSerializer(many=True)
    expiring_documents = ExpiringDocumentSerializer(many=True)
    notifications = NotificationSerializer(many=True)
    recent_applications = ProcessingApplicationSerializer(many=True)
    open_alerts = EnvironmentalAlertSerializer(many=True)
    open_incidents = IncidentSerializer(many=True)
