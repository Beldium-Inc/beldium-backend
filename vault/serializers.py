from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from vault.models import ReceivedFrom, VaultActivity, VaultCategory, VaultDocument

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "csv", "jpg", "jpeg", "png", "zip"}


def max_upload_bytes():
    return getattr(settings, "VAULT_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)


def validate_vault_file(value):
    limit = max_upload_bytes()
    if value.size > limit:
        raise serializers.ValidationError(f"File too large; the maximum size is {limit // (1024 * 1024)} MB.")
    if value.size == 0:
        raise serializers.ValidationError("The file is empty.")
    extension = Path(value.name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        raise serializers.ValidationError("Only PDF, Word, Excel, CSV, ZIP, JPEG and PNG files are supported.")
    return value


def _person(user):
    if not user:
        return ""
    return (getattr(user, "full_name", "") or "").strip() or user.email


class VaultCategorySerializer(serializers.ModelSerializer):
    document_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = VaultCategory
        fields = ["id", "code", "name", "description", "document_count"]


class VaultDocumentSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    organisation = serializers.SerializerMethodField()
    uploaded_by = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    approved_by = serializers.SerializerMethodField()
    approved_at = serializers.SerializerMethodField()
    is_latest = serializers.BooleanField(read_only=True)

    class Meta:
        model = VaultDocument
        fields = [
            "id", "reference", "sector", "category", "organisation", "family", "version", "is_latest",
            "title", "description", "version_note", "original_name", "content_type", "size_bytes", "sha256",
            "sniffed_type", "scan_status", "on_behalf_of_company", "received_from", "received_date",
            "uploaded_by", "created_at", "status", "approved_by", "approved_at",
        ]

    def get_category(self, obj):
        return {"code": obj.category.code, "name": obj.category.name}

    def get_organisation(self, obj):
        if not obj.organisation_id:
            return {"id": None, "name": "Beldium"}
        return {"id": str(obj.organisation_id), "name": obj.organisation.name}

    def get_uploaded_by(self, obj):
        return _person(obj.uploaded_by)

    def _approval(self, obj):
        return getattr(obj, "approval", None)

    def get_status(self, obj):
        return "approved" if self._approval(obj) else "awaiting_approval"

    def get_approved_by(self, obj):
        approval = self._approval(obj)
        return _person(approval.approved_by) if approval else ""

    def get_approved_at(self, obj):
        approval = self._approval(obj)
        return approval.created_at if approval else None


class VaultActivitySerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()
    reference = serializers.CharField(source="document.reference")
    version = serializers.IntegerField(source="document.version")

    class Meta:
        model = VaultActivity
        fields = ["id", "created_at", "actor", "action", "detail", "reference", "version"]

    def get_actor(self, obj):
        return _person(obj.actor)


class VaultUploadSerializer(serializers.Serializer):
    sector = serializers.CharField()
    category = serializers.SlugField()
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    organisation = serializers.UUIDField(required=False, allow_null=True, default=None)
    file = serializers.FileField(validators=[validate_vault_file])
    # Required when the desk files for an organisation (on behalf of it).
    received_from = serializers.ChoiceField(choices=ReceivedFrom.choices, required=False, allow_blank=True, default="")
    received_date = serializers.DateField(required=False, allow_null=True, default=None)


class VaultVersionSerializer(serializers.Serializer):
    file = serializers.FileField(validators=[validate_vault_file])
    note = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
    received_from = serializers.ChoiceField(choices=ReceivedFrom.choices, required=False, allow_blank=True, default="")
    received_date = serializers.DateField(required=False, allow_null=True, default=None)


class VaultApproveSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
