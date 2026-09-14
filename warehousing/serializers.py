from pathlib import Path

from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from warehousing import models as m


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
    def validate_warehouse(self, value):
        if self.instance and value.pk != self.instance.warehouse_id:
            raise serializers.ValidationError("The warehouse cannot be changed.")
        return value


class WarehouseOperatorSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="organisation.name", read_only=True)
    registration_number = serializers.CharField(source="organisation.registration_number", read_only=True)
    services = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)
    storage_categories = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)

    class Meta:
        model = m.WarehouseOperator
        fields = "__all__"
        read_only_fields = ["id", "reference", "created_at", "updated_at"]

    def validate_services(self, value):
        return strings(value)

    def validate_storage_categories(self, value):
        return strings(value)

    def validate_organisation(self, value):
        if self.instance and value.pk != self.instance.organisation_id:
            raise serializers.ValidationError("The organisation cannot be changed.")
        return value


class FacilitySerializer(OwnedSerializer):
    class Meta:
        model = m.Facility
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class StorageZoneSerializer(OwnedSerializer):
    class Meta:
        model = m.StorageZone
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        warehouse = attrs.get("warehouse", getattr(self.instance, "warehouse", None))
        facility = attrs.get("facility", getattr(self.instance, "facility", None))
        if facility and facility.warehouse_id != warehouse.pk:
            raise serializers.ValidationError({"facility": "This facility belongs to another warehouse."})
        min_temp = attrs.get("temperature_min", getattr(self.instance, "temperature_min", None))
        max_temp = attrs.get("temperature_max", getattr(self.instance, "temperature_max", None))
        if min_temp is not None and max_temp is not None and max_temp < min_temp:
            raise serializers.ValidationError({"temperature_max": "Maximum temperature cannot be lower than minimum temperature."})
        return attrs


class InventoryLotSerializer(OwnedSerializer):
    class Meta:
        model = m.InventoryLot
        fields = "__all__"
        read_only_fields = ["id", "reference", "created_at", "updated_at"]

    def validate(self, attrs):
        warehouse = attrs.get("warehouse", getattr(self.instance, "warehouse", None))
        for field in ["facility", "zone"]:
            obj = attrs.get(field, getattr(self.instance, field, None))
            if obj and obj.warehouse_id != warehouse.pk:
                raise serializers.ValidationError({field: "This record belongs to another warehouse."})
        facility = attrs.get("facility", getattr(self.instance, "facility", None))
        zone = attrs.get("zone", getattr(self.instance, "zone", None))
        if facility and zone and zone.facility_id != facility.pk:
            raise serializers.ValidationError({"zone": "Choose a zone in the selected facility."})
        received_on = attrs.get("received_on", getattr(self.instance, "received_on", None))
        expires_on = attrs.get("expires_on", getattr(self.instance, "expires_on", None))
        if received_on and expires_on and expires_on < received_on:
            raise serializers.ValidationError({"expires_on": "Expiry cannot precede receipt date."})
        return attrs


class InspectionSerializer(OwnedSerializer):
    class Meta:
        model = m.Inspection
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        warehouse = attrs.get("warehouse", getattr(self.instance, "warehouse", None))
        facility = attrs.get("facility", getattr(self.instance, "facility", None))
        if facility and facility.warehouse_id != warehouse.pk:
            raise serializers.ValidationError({"facility": "This facility belongs to another warehouse."})
        inspected_on = attrs.get("inspected_on", getattr(self.instance, "inspected_on", None))
        next_due_on = attrs.get("next_due_on", getattr(self.instance, "next_due_on", None))
        if inspected_on and next_due_on and next_due_on < inspected_on:
            raise serializers.ValidationError({"next_due_on": "Next due date cannot precede inspection date."})
        return attrs


class GrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.WarehousingAccessGrant
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
        model = m.WarehousingCondition
        fields = ["id", "title", "description", "due_date", "domain", "cleared_at", "is_overdue"]
        read_only_fields = ["id", "cleared_at", "is_overdue"]

    def validate_due_date(self, value):
        return future_date(value)

    def get_is_overdue(self, obj):
        return not obj.cleared_at and obj.due_date < timezone.localdate()


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.WarehousingDocument
        fields = ["domain", "document_type", "title", "issuer", "reference", "issued_on", "expires_on", "lot", "condition", "file"]

    def validate_file(self, value):
        return validate_file(value)

    def validate(self, attrs):
        application = self.context["application"]
        lot = attrs.get("lot")
        if lot and lot.warehouse_id != application.warehouse_id:
            raise serializers.ValidationError({"lot": "This lot belongs to another warehouse."})
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
        model = m.WarehousingDocument
        exclude = ["file"]
        read_only_fields = [field.name for field in m.WarehousingDocument._meta.fields]

    def get_download_url(self, obj):
        return self.context["request"].build_absolute_uri(reverse("warehousing-document-download", args=[obj.pk]))

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
        model = m.WarehousingApplication
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at", "created_by", "reviewer", "status", "submitted_at", "reviewed_at", "rationale", "policy_version", "domain_weights"]

    def get_progress(self, obj):
        from warehousing.services import progress
        return progress(obj)

    def get_risk(self, obj):
        from warehousing.services import risk
        return risk(obj)


class AssignReviewerSerializer(serializers.Serializer):
    reviewer = serializers.PrimaryKeyRelatedField(queryset=m.WarehousingApplication._meta.get_field("reviewer").remote_field.model.objects.filter(is_active=True))


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
        ref_name = "WarehousingNotification"
        fields = ["id", "warehouse", "title", "body", "read_at", "created_at"]
        read_only_fields = fields


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.WarehousingReport
        ref_name = "WarehousingReport"
        fields = ["id", "report_type", "created_at"]
        read_only_fields = fields


class SummarySerializer(serializers.Serializer):
    data = serializers.DictField()
