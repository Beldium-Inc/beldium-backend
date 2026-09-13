"""DRF serialisers for the Mining Compliance register.

Field names are the model's, so the frontend's generated types stay a
faithful mirror. Mirrors ``processing.serializers`` closely — see
``mining.models`` for why the two verticals stay separate apps.
"""
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import AccountAuditEvent
from mining.models import (
    Application,
    CorrectiveSubmission,
    DocumentRecord,
    Equipment,
    EnvRecord,
    Evidence,
    InfoRequest,
    Inspection,
    InventoryItem,
    LicenceDoc,
    MineSite,
    MiningOrganisationProfile,
    NonConformity,
    PendingReview,
    ProductionRecord,
    ReviewSection,
    Sample,
    SafetyIncident,
    ScoreFactor,
    SectionKey,
)

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


def actor_name(user):
    if user is None:
        return "System"
    return user.full_name or user.email


# --- organisation profile -----------------------------------------------------


class MiningOrganisationProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MiningOrganisationProfile
        fields = ["id", "organisation", "directors", "beneficial_owners", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


# --- sites ---------------------------------------------------------------------


class ScoreFactorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScoreFactor
        fields = ["id", "site", "label", "weight", "score", "reason", "trend", "created_at"]
        read_only_fields = ["id", "created_at"]


class EvidenceSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    original_name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Evidence
        fields = [
            "id", "section", "name", "kind", "file", "file_url", "original_name",
            "status", "uploaded_by_name", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "file_url", "original_name", "uploaded_by_name", "created_at", "updated_at"]
        extra_kwargs = {"file": {"write_only": True, "required": False}, "section": {"required": False}}

    def validate_file(self, value):
        return validate_upload(value)

    def get_uploaded_by_name(self, obj) -> str:
        return actor_name(obj.uploaded_by)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        url = reverse("mining-site-download-evidence", args=[obj.section.site_id, obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ReviewSectionSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()
    evidence = EvidenceSerializer(many=True, read_only=True)

    class Meta:
        model = ReviewSection
        fields = [
            "id", "site", "key", "label", "title", "summary", "weight", "score", "status",
            "fields", "decision_note", "decided_by_name", "decided_at", "evidence",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "label", "status", "decision_note", "decided_by_name", "decided_at",
            "evidence", "created_at", "updated_at",
        ]

    def get_label(self, obj) -> str:
        return SectionKey(obj.key).label

    def get_decided_by_name(self, obj) -> str | None:
        return actor_name(obj.decided_by) if obj.decided_by_id else None

    def validate_fields(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Section fields must be a list.")
        for entry in value:
            if not isinstance(entry, dict) or "label" not in entry:
                raise serializers.ValidationError("Each field needs at least a label.")
            if set(entry) - {"label", "value", "flag", "note"}:
                raise serializers.ValidationError("A field accepts only label, value, flag and note.")
            if entry.get("flag") not in {None, "ok", "warn", "bad"}:
                raise serializers.ValidationError("flag must be one of ok, warn, bad.")
        return value


class MiningSectionReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["verified", "rejected", "info_requested", "flagged", "under_review", "pending"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    score = serializers.IntegerField(required=False, min_value=0, max_value=100)


class MineSiteSerializer(serializers.ModelSerializer):
    compliance_percent = serializers.SerializerMethodField()
    open_non_conformities = serializers.SerializerMethodField()
    score_factors = ScoreFactorSerializer(many=True, read_only=True)

    class Meta:
        model = MineSite
        fields = [
            "id", "code", "organisation", "name", "mineral", "state", "lga",
            "latitude", "longitude", "area_ha", "status", "compliance_score", "risk",
            "capacity_tpa", "current_tpa", "workforce", "last_inspection_on",
            "verification", "risk_reasons", "production", "inventory", "transactions",
            "compliance_percent", "open_non_conformities", "score_factors",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "code", "compliance_score", "compliance_percent", "open_non_conformities",
            "score_factors", "created_at", "updated_at",
        ]

    def get_compliance_percent(self, obj) -> int:
        from mining import scoring

        return scoring.completeness(obj)

    def get_open_non_conformities(self, obj) -> int:
        return sum(1 for nc in obj.non_conformities.all() if nc.status != NonConformity.Status.CLOSED)

    def validate_organisation(self, value):
        if self.instance and value != self.instance.organisation:
            raise serializers.ValidationError("A site's organisation cannot be changed.")
        return value


class MineSiteDetailSerializer(MineSiteSerializer):
    sections = ReviewSectionSerializer(many=True, read_only=True)
    review = serializers.SerializerMethodField()
    outstanding = serializers.SerializerMethodField()

    class Meta(MineSiteSerializer.Meta):
        fields = MineSiteSerializer.Meta.fields + ["sections", "review", "outstanding"]
        read_only_fields = MineSiteSerializer.Meta.read_only_fields + ["sections", "review", "outstanding"]

    def get_review(self, obj) -> dict:
        from mining import scoring

        return scoring.review_summary(obj)

    def get_outstanding(self, obj) -> list:
        from mining import scoring

        return scoring.outstanding(obj)


# --- licences & documents -------------------------------------------------------


class LicenceDocSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    days_to_expiry = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = LicenceDoc
        fields = [
            "id", "site", "site_name", "number", "type", "authority", "issued_on",
            "expires_on", "status", "days_to_expiry", "file", "file_url", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "site_name", "days_to_expiry", "file_url", "created_at", "updated_at"]
        extra_kwargs = {"file": {"write_only": True, "required": False}}

    def validate_file(self, value):
        return validate_upload(value)

    def get_days_to_expiry(self, obj) -> int | None:
        if not obj.expires_on:
            return None
        return (obj.expires_on - timezone.localdate()).days

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        url = reverse("mining-licence-download", args=[obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class DocumentRecordSerializer(serializers.ModelSerializer):
    original_name = serializers.CharField(read_only=True)
    file_url = serializers.SerializerMethodField()
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = DocumentRecord
        fields = [
            "id", "site", "name", "category", "expires_on", "status",
            "file", "file_url", "original_name", "uploaded_by_name", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "file_url", "original_name", "uploaded_by_name", "created_at", "updated_at"]
        extra_kwargs = {"file": {"write_only": True, "required": False}}

    def validate_file(self, value):
        return validate_upload(value)

    def get_uploaded_by_name(self, obj) -> str:
        return actor_name(obj.uploaded_by)

    @extend_schema_field(OpenApiTypes.URI)
    def get_file_url(self, obj) -> str | None:
        if not obj.file:
            return None
        url = reverse("mining-document-download", args=[obj.id])
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


# --- findings --------------------------------------------------------------------


class CorrectiveSubmissionSerializer(serializers.ModelSerializer):
    submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CorrectiveSubmission
        fields = [
            "id", "non_conformity", "message", "submitted_by_name", "file",
            "decision", "decision_note", "decided_at", "created_at",
        ]
        read_only_fields = ["id", "submitted_by_name", "decision", "decision_note", "decided_at", "created_at"]
        extra_kwargs = {"non_conformity": {"required": False}}

    def get_submitted_by_name(self, obj) -> str:
        return actor_name(obj.submitted_by)


class NonConformitySerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)
    submissions = CorrectiveSubmissionSerializer(many=True, read_only=True)

    class Meta:
        model = NonConformity
        fields = [
            "id", "reference", "site", "site_name", "title", "category", "severity",
            "required_action", "responsible_person", "deadline", "status", "is_overdue",
            "submissions", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "site_name", "status", "is_overdue", "submissions", "created_at", "updated_at"]

    def validate_deadline(self, value):
        if value < timezone.localdate():
            raise serializers.ValidationError("The corrective-action deadline cannot be in the past.")
        return value


class NonConformityClosureSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


# --- operations --------------------------------------------------------------------


class InspectionSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    inspector_display = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = [
            "id", "reference", "site", "site_name", "type", "scheduled_for", "inspector",
            "inspector_name", "inspector_display", "status", "result", "findings", "notes",
            "completed_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "site_name", "inspector_display", "completed_at", "created_at", "updated_at"]

    def get_inspector_display(self, obj) -> str:
        if obj.inspector_id:
            return actor_name(obj.inspector)
        return obj.inspector_name or "Unassigned"


class InspectionRequestSerializer(serializers.Serializer):
    scheduled_for = serializers.DateField(required=False, allow_null=True)
    type = serializers.ChoiceField(choices=Inspection.Type.choices, default=Inspection.Type.PRE_APPROVAL)
    inspector_name = serializers.CharField(required=False, allow_blank=True, max_length=200)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate_scheduled_for(self, value):
        if value and value < timezone.localdate():
            raise serializers.ValidationError("An inspection cannot be scheduled in the past.")
        return value


class SampleSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = Sample
        fields = [
            "id", "reference", "site", "site_name", "collected_on", "lab", "certificate",
            "li2o_percent", "fe2o3_percent", "moisture_percent", "status", "method",
            "chain_of_custody", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "site_name", "created_at", "updated_at"]


class EnvRecordSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = EnvRecord
        fields = ["id", "site", "site_name", "metric", "value", "limit", "status", "measured_on", "created_at", "updated_at"]
        read_only_fields = ["id", "site_name", "created_at", "updated_at"]


class SafetyIncidentSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = SafetyIncident
        fields = ["id", "site", "site_name", "date", "type", "severity", "lost_days", "status", "summary", "created_at", "updated_at"]
        read_only_fields = ["id", "site_name", "created_at", "updated_at"]


class EquipmentSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = Equipment
        fields = ["id", "site", "site_name", "name", "serial", "cert_expires_on", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "site_name", "created_at", "updated_at"]


class ProductionRecordSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = ProductionRecord
        fields = [
            "id", "site", "site_name", "period_start", "period_end", "commodity",
            "tonnage", "grade", "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "site_name", "created_at", "updated_at"]

    def validate(self, attrs):
        period_start = attrs.get("period_start", getattr(self.instance, "period_start", None))
        period_end = attrs.get("period_end", getattr(self.instance, "period_end", None))
        if period_start and period_end and period_end < period_start:
            raise serializers.ValidationError("period_end cannot be before period_start.")
        return attrs


class InventoryItemSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id", "site", "site_name", "category", "name", "quantity", "unit",
            "threshold", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "site_name", "created_at", "updated_at"]


class ApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = [
            "id", "reference", "organisation", "site", "site_name", "type", "mineral",
            "submitted_on", "stage", "status", "assigned_to", "sla_days", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "status", "created_at", "updated_at"]

    def validate_organisation(self, value):
        if self.instance and value != self.instance.organisation:
            raise serializers.ValidationError("An application's organisation cannot be changed.")
        return value


class PendingReviewSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = PendingReview
        fields = [
            "id", "site", "site_name", "subject", "type", "priority", "submitted_on",
            "due_on", "status", "assigned_to", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "site_name", "created_at", "updated_at"]


class InfoRequestSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    requested_by_name = serializers.SerializerMethodField()

    class Meta:
        model = InfoRequest
        fields = [
            "id", "site", "site_name", "section", "subject", "details", "requested_by_name",
            "due_by", "priority", "status", "response_message", "response_at",
            "response_attachments", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "site_name", "requested_by_name", "status", "response_message",
            "response_at", "response_attachments", "created_at", "updated_at",
        ]

    def get_requested_by_name(self, obj) -> str:
        return actor_name(obj.requested_by)


class InfoRequestResponseSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)


# --- audit ---------------------------------------------------------------------


class MiningAuditEventSerializer(serializers.ModelSerializer):
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
        return obj.event_type.removeprefix("mining.").replace("_", " ").capitalize()

    def get_target(self, obj) -> str:
        return (obj.metadata or {}).get("target", "")

    def get_detail(self, obj) -> str:
        return (obj.metadata or {}).get("detail", "")


# --- dashboard (existing, kept as-is) --------------------------------------------


class ExpiringLicenceSerializer(serializers.ModelSerializer):
    """Register-wide licence/permit expiry watchlist row."""

    site_name = serializers.CharField(source="site.name", read_only=True)
    days_to_expiry = serializers.SerializerMethodField()

    class Meta:
        model = LicenceDoc
        fields = ["id", "number", "type", "site_name", "authority", "expires_on", "days_to_expiry", "status"]

    def get_days_to_expiry(self, obj) -> int | None:
        if not obj.expires_on:
            return None

        return (obj.expires_on - timezone.localdate()).days


class DashboardTotalsSerializer(serializers.Serializer):
    sites = serializers.IntegerField()
    operational_sites = serializers.IntegerField()
    suspended_sites = serializers.IntegerField()
    applications = serializers.IntegerField()
    applications_pending = serializers.IntegerField()
    applications_under_review = serializers.IntegerField()
    open_non_conformities = serializers.IntegerField()
    overdue_non_conformities = serializers.IntegerField()
    upcoming_inspections = serializers.IntegerField()
    environmental_watch = serializers.IntegerField()
    environmental_breaches = serializers.IntegerField()
    open_safety_incidents = serializers.IntegerField()
    expiring_licences = serializers.IntegerField()
    average_compliance_score = serializers.IntegerField()


class DashboardSerializer(serializers.Serializer):
    audience = serializers.CharField()
    capabilities = serializers.DictField()
    totals = DashboardTotalsSerializer()
    kpi_trend = serializers.ListField()
    regional_compliance = serializers.ListField()
    expiring_licences = serializers.ListField()
    notifications = serializers.ListField()
    recent_applications = serializers.ListField()
    open_environmental_records = serializers.ListField()
    open_incidents = serializers.ListField()
