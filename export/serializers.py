from pathlib import Path

from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from export import models as m


def strings(value):
    if not isinstance(value, list) or not value or len(value) > 100:
        raise serializers.ValidationError("Supply between 1 and 100 values.")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in value):
        raise serializers.ValidationError("Values must be nonblank strings of at most 100 characters.")
    if len({item.strip().casefold() for item in value}) != len(value):
        raise serializers.ValidationError("Duplicate values are not allowed.")
    return [item.strip() for item in value]


def future_date(value):
    if value < timezone.localdate():
        raise serializers.ValidationError("The deadline cannot be in the past.")
    return value


def validate_file(value):
    allowed = {
        ".pdf": {"application/pdf"},
        ".doc": {"application/msword"},
        ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        ".jpg": {"image/jpeg"},
        ".jpeg": {"image/jpeg"},
        ".png": {"image/png"},
        ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ".zip": {"application/zip", "application/x-zip-compressed"},
    }
    suffix = Path(value.name).suffix.lower()
    if suffix not in allowed or value.content_type not in allowed[suffix]:
        raise serializers.ValidationError("Use PDF, Word, JPEG, PNG, XLSX or ZIP with the matching content type.")
    if not 0 < value.size <= 20 * 1024 * 1024:
        raise serializers.ValidationError("Files must be nonempty and no larger than 20 MB.")
    return value


class OwnedSerializer(serializers.ModelSerializer):
    def validate_exporter(self, value):
        if self.instance and value.pk != self.instance.exporter_id:
            raise serializers.ValidationError("The exporter cannot be changed.")
        return value


class ExporterSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="organisation.name", read_only=True)
    registration_number = serializers.CharField(source="organisation.registration_number", read_only=True)
    destinations = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)
    product_categories = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)

    class Meta:
        model = m.Exporter
        fields = "__all__"
        read_only_fields = ["id", "reference", "created_at", "updated_at"]

    def validate_destinations(self, value):
        return strings(value)

    def validate_product_categories(self, value):
        return strings(value)

    def validate_organisation(self, value):
        if self.instance and value.pk != self.instance.organisation_id:
            raise serializers.ValidationError("The organisation cannot be changed.")
        return value


class ProductSerializer(OwnedSerializer):
    class Meta:
        model = m.Product
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class BuyerSerializer(OwnedSerializer):
    class Meta:
        model = m.Buyer
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class ShipmentSerializer(OwnedSerializer):
    class Meta:
        model = m.Shipment
        fields = "__all__"
        read_only_fields = ["id", "reference", "created_at", "updated_at"]

    def validate(self, attrs):
        exporter = attrs.get("exporter", getattr(self.instance, "exporter", None))
        for field in ["product", "buyer"]:
            obj = attrs.get(field, getattr(self.instance, field, None))
            if obj and obj.exporter_id != exporter.pk:
                raise serializers.ValidationError({field: "This record belongs to another exporter."})
        return attrs


class GrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ExportAccessGrant
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class DomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.DomainReview
        fields = ["key", "data", "applicable", "status", "score", "review_notes", "reviewed_at"]
        read_only_fields = fields


class SectionInputSerializer(serializers.Serializer):
    data = serializers.DictField(allow_empty=False)


class DomainReviewInputSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["passed", "attention", "failed"])
    score = serializers.IntegerField(min_value=0, max_value=100)
    notes = serializers.CharField()
    applicable = serializers.BooleanField(default=True)


class ConditionSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = m.ExportCondition
        fields = ["id", "title", "description", "due_date", "domain", "cleared_at", "is_overdue"]
        read_only_fields = ["id", "cleared_at", "is_overdue"]

    def validate_due_date(self, value):
        return future_date(value)

    def get_is_overdue(self, obj):
        return not obj.cleared_at and obj.due_date < timezone.localdate()


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ExportDocument
        fields = ["domain", "document_type", "title", "issuer", "reference", "issued_on", "expires_on", "shipment", "condition", "file"]

    def validate_file(self, value):
        return validate_file(value)

    def validate(self, attrs):
        application = self.context["application"]
        shipment = attrs.get("shipment")
        if shipment and shipment.exporter_id != application.exporter_id:
            raise serializers.ValidationError({"shipment": "This shipment belongs to another exporter."})
        condition = attrs.get("condition")
        if condition and (condition.application_id != application.pk or condition.cleared_at):
            raise serializers.ValidationError({"condition": "Choose an outstanding condition for this application."})
        if attrs.get("issued_on") and attrs.get("expires_on") and attrs["expires_on"] < attrs["issued_on"]:
            raise serializers.ValidationError({"expires_on": "Expiry cannot precede issue date."})
        return attrs


class DocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    validity = serializers.SerializerMethodField()

    class Meta:
        model = m.ExportDocument
        exclude = ["file"]
        read_only_fields = [field.name for field in m.ExportDocument._meta.fields]

    def get_download_url(self, obj):
        return self.context["request"].build_absolute_uri(reverse("export-document-download", args=[obj.pk]))

    def get_validity(self, obj):
        if not obj.expires_on:
            return "current"
        days = (obj.expires_on - timezone.localdate()).days
        return "expired" if days < 0 else "expiring" if days <= 30 else "current"


class ApplicationSerializer(OwnedSerializer):
    sections = DomainSerializer(many=True, read_only=True)
    conditions = ConditionSerializer(many=True, read_only=True)
    progress = serializers.SerializerMethodField()
    risk = serializers.SerializerMethodField()

    class Meta:
        model = m.ExportApplication
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "created_by", "reviewer", "status", "submitted_at", "reviewed_at", "rationale", "policy_version", "domain_weights"]

    def get_progress(self, obj):
        from export.services import progress
        return progress(obj)

    def get_risk(self, obj):
        from export.services import risk
        return risk(obj)


class AssignReviewerSerializer(serializers.Serializer):
    reviewer = serializers.PrimaryKeyRelatedField(queryset=m.ExportApplication._meta.get_field("reviewer").remote_field.model.objects.filter(is_active=True))


class ReviewDocumentInputSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["verified", "rejected"])
    notes = serializers.CharField()


class DecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["approved", "conditionally_approved", "rejected"])
    rationale = serializers.CharField()
    conditions = ConditionSerializer(many=True, required=False)

    def validate(self, attrs):
        if attrs.get("conditions") and attrs["status"] != "conditionally_approved":
            raise serializers.ValidationError("Conditions may only accompany conditional approval.")
        return attrs


class RequestResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.RequestResponse
        fields = ["id", "message", "documents", "author", "created_at"]
        read_only_fields = ["id", "author", "created_at"]


class InformationRequestSerializer(serializers.ModelSerializer):
    responses = RequestResponseSerializer(many=True, read_only=True)
    is_overdue = serializers.SerializerMethodField()
    items = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)

    class Meta:
        model = m.InformationRequest
        fields = ["id", "application", "reason", "message", "items", "due_date", "status", "raised_by", "review_notes", "responses", "is_overdue", "created_at"]
        read_only_fields = ["id", "application", "status", "raised_by", "review_notes", "created_at"]

    def validate_items(self, value):
        return strings(value)

    def validate_due_date(self, value):
        return future_date(value)

    def get_is_overdue(self, obj):
        return obj.status != "accepted" and obj.due_date < timezone.localdate()


class ResponseReviewSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    notes = serializers.CharField()


class ResolutionSerializer(serializers.Serializer):
    notes = serializers.CharField()


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Notification
        ref_name = "ExportNotification"
        fields = ["id", "exporter", "title", "body", "read_at", "created_at"]
        read_only_fields = fields


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ExportReport
        ref_name = "ExportReport"
        fields = ["id", "report_type", "created_at"]
        read_only_fields = fields


class SummarySerializer(serializers.Serializer):
    data = serializers.DictField()
