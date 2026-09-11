from pathlib import Path

from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from marketplace import models as m, services


def strings(value):
    if not isinstance(value, list) or len(value) > 50:
        raise serializers.ValidationError("Supply a list of up to 50 values.")
    if any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in value):
        raise serializers.ValidationError("Values must be nonblank strings of at most 100 characters.")
    return [item.strip() for item in value]


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


class SellerSerializer(serializers.ModelSerializer):
    organisation_name = serializers.CharField(source="organisation.name", read_only=True)
    service_regions = serializers.ListField(child=serializers.CharField(max_length=100), required=False)

    class Meta:
        model = m.SellerProfile
        ref_name = "MarketplaceSeller"
        fields = "__all__"
        read_only_fields = ["id", "reference", "status", "reviewed_by", "reviewed_at", "review_notes", "trust_score", "created_at", "updated_at"]

    def validate_service_regions(self, value):
        return strings(value)


class SellerDecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["verified", "restricted", "suspended", "rejected"])
    notes = serializers.CharField(required=False, allow_blank=True)
    trust_score = serializers.IntegerField(min_value=0, max_value=100, required=False)


class AccessGrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MarketplaceAccessGrant
        ref_name = "MarketplaceAccessGrant"
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class ProductSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source="seller.display_name", read_only=True)
    progress = serializers.SerializerMethodField()
    risk = serializers.SerializerMethodField()

    class Meta:
        model = m.MarketplaceProduct
        ref_name = "MarketplaceProduct"
        fields = "__all__"
        read_only_fields = ["id", "reference", "status", "compliance_score", "risk_band", "published_at", "reviewed_by", "reviewed_at", "review_notes", "created_at", "updated_at"]

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_progress(self, obj):
        return services.product_progress(obj)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_risk(self, obj):
        return {"compliance_score": obj.compliance_score, "risk_band": obj.risk_band}


class ProductSubmitSerializer(serializers.Serializer):
    pass


class ProductReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "changes_required", "restricted", "suspended"])
    notes = serializers.CharField(required=False, allow_blank=True)


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ProductDocument
        ref_name = "MarketplaceDocumentUpload"
        fields = ["document_type", "title", "issuer", "reference", "issued_on", "expires_on", "file"]

    def validate_file(self, value):
        return validate_file(value)

    def validate(self, attrs):
        if attrs.get("issued_on") and attrs.get("expires_on") and attrs["expires_on"] < attrs["issued_on"]:
            raise serializers.ValidationError({"expires_on": "Expiry cannot precede issue date."})
        return attrs


class DocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    validity = serializers.SerializerMethodField()

    class Meta:
        model = m.ProductDocument
        ref_name = "MarketplaceDocument"
        exclude = ["file"]
        read_only_fields = [field.name for field in m.ProductDocument._meta.fields]

    @extend_schema_field(OpenApiTypes.URI)
    def get_download_url(self, obj) -> str:
        return self.context["request"].build_absolute_uri(reverse("marketplace-document-download", args=[obj.pk]))

    @extend_schema_field(OpenApiTypes.STR)
    def get_validity(self, obj) -> str:
        if not obj.expires_on:
            return "current"
        days = (obj.expires_on - timezone.localdate()).days
        return "expired" if days < 0 else "expiring" if days <= 30 else "current"


class DocumentReviewSerializer(serializers.Serializer):
    class Meta:
        ref_name = "MarketplaceDocumentReview"

    status = serializers.ChoiceField(choices=["verified", "rejected"])
    notes = serializers.CharField(required=False, allow_blank=True)


class ComplianceCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.ProductComplianceCheck
        ref_name = "MarketplaceComplianceCheck"
        fields = ["id", "key", "label", "status", "score", "notes", "reviewed_at"]
        read_only_fields = ["id", "key", "label", "reviewed_at"]


class ComplianceCheckReviewSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["passed", "attention", "failed"])
    score = serializers.IntegerField(min_value=0, max_value=100)
    notes = serializers.CharField(required=False, allow_blank=True)


class OrderSerializer(serializers.ModelSerializer):
    total = serializers.SerializerMethodField()

    class Meta:
        model = m.MarketplaceOrder
        ref_name = "MarketplaceOrder"
        fields = "__all__"
        read_only_fields = ["id", "reference", "buyer", "unit_price", "currency", "status", "payment_status", "placed_at", "fulfilled_at", "created_at", "updated_at"]

    @extend_schema_field(OpenApiTypes.STR)
    def get_total(self, obj):
        return str(obj.quantity * obj.unit_price)

    def validate(self, attrs):
        product = attrs["product"]
        if product.status != m.ListingStatus.ACTIVE:
            raise serializers.ValidationError({"product": "Only active listings can be ordered."})
        if attrs["quantity"] > product.quantity_available:
            raise serializers.ValidationError({"quantity": "Requested quantity exceeds available listing quantity."})
        attrs["seller"] = product.seller
        return attrs


class FulfillOrderSerializer(serializers.Serializer):
    shipping_reference = serializers.CharField(max_length=120)


class PaymentWebhookSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=50)
    event_id = serializers.CharField(max_length=120)
    event_type = serializers.CharField(max_length=80)
    payment_reference = serializers.CharField(max_length=120)
    status = serializers.ChoiceField(choices=["paid", "failed", "refunded"])
    payload = serializers.DictField(required=False)


class LicenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MarketplaceLicense
        ref_name = "MarketplaceLicense"
        fields = "__all__"
        read_only_fields = ["id", "created_at", "updated_at"]


class DisputeSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MarketplaceDispute
        ref_name = "MarketplaceDispute"
        fields = "__all__"
        read_only_fields = ["id", "raised_by", "resolution", "resolved_by", "resolved_at", "created_at", "updated_at"]


class DisputeResolutionSerializer(serializers.Serializer):
    resolution = serializers.CharField()


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MarketplaceNotification
        ref_name = "MarketplaceNotification"
        fields = ["id", "seller", "title", "body", "read_at", "created_at"]
        read_only_fields = fields


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MarketplaceAuditEvent
        ref_name = "MarketplaceAuditEvent"
        fields = "__all__"
        read_only_fields = [field.name for field in m.MarketplaceAuditEvent._meta.fields]


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MarketplaceReport
        ref_name = "MarketplaceReport"
        fields = ["id", "report_type", "created_at"]
        read_only_fields = fields


class SummarySerializer(serializers.Serializer):
    class Meta:
        ref_name = "MarketplaceSummary"

    data = serializers.DictField()
