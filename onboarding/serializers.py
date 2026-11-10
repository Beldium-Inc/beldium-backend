import re

from django.conf import settings
from rest_framework import serializers

from compliance.serializers import validate_upload
from onboarding.models import (
    ExternalPortal,
    OnboardingOption,
    ParticipantApplication,
    ParticipantDocument,
    RegulatorAccessRequest,
)
from onboarding.participants import REQUIREMENTS, TYPE_FOR_SECTOR, missing_items
from organisations.models import ADMIN_ROLES, SECTOR_SLUGS, ParticipantSubtype


def portal_url(portal):
    if portal == ExternalPortal.MINER:
        return settings.MINER_PORTAL_URL
    if portal == ExternalPortal.LOGISTICS:
        return settings.LOGISTICS_PORTAL_URL
    return ""


class OnboardingOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = OnboardingOption
        fields = ["key", "kind", "label", "description", "role", "org_path", "external_url", "external_label", "note"]

    external_url = serializers.SerializerMethodField()

    def get_external_url(self, obj) -> str:
        return portal_url(obj.external_portal)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        # The optional keys are left out when empty, as the frontend's
        # OnboardingOption type has them.
        for key in ("org_path", "external_url", "external_label", "note"):
            if not data.get(key):
                data.pop(key, None)
        return data


# Participant application

def organisation_summary(organisation):
    return {
        "id": str(organisation.id),
        "beldium_id": organisation.beldium_id,
        "name": organisation.name,
        "organisation_type": organisation.organisation_type,
        "participant_subtype": organisation.participant_subtype,
        "verification_status": organisation.verification_status,
    }


class ParticipantDocumentSerializer(serializers.ModelSerializer):
    file_name = serializers.CharField(source="original_name", read_only=True)

    class Meta:
        model = ParticipantDocument
        fields = ["id", "title", "file_name", "created_at", "updated_at"]


class ParticipantApplicationSerializer(serializers.ModelSerializer):
    organisation = serializers.SerializerMethodField()
    documents = ParticipantDocumentSerializer(many=True, read_only=True)
    my_role = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    missing = serializers.SerializerMethodField()

    class Meta:
        model = ParticipantApplication
        fields = [
            "id", "sector", "status", "participant_subtype", "organisation",
            "company", "operations", "sites", "representative", "declarations",
            "documents", "my_role", "can_submit", "missing",
            "submitted_at", "created_at", "updated_at",
        ]
        read_only_fields = fields

    def _membership(self, obj):
        request = self.context.get("request")
        if not request:
            return None
        return obj.organisation.memberships.filter(user=request.user, is_active=True).first()

    def get_organisation(self, obj) -> dict:
        return organisation_summary(obj.organisation)

    def get_my_role(self, obj) -> str | None:
        membership = self._membership(obj)
        return membership.role if membership else None

    def get_can_submit(self, obj) -> bool:
        membership = self._membership(obj)
        return bool(
            obj.status == ParticipantApplication.Status.DRAFT
            and membership and membership.role in ADMIN_ROLES
        )

    def get_missing(self, obj) -> list[str]:
        return missing_items(obj)


class ParticipantRegisterSerializer(serializers.Serializer):
    sector = serializers.ChoiceField(choices=sorted(TYPE_FOR_SECTOR))
    name = serializers.CharField(max_length=255)
    registration_number = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    participant_subtype = serializers.ChoiceField(choices=ParticipantSubtype.choices, required=False, allow_blank=True, default="")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter the company's name.")
        return value

    def validate(self, attrs):
        if attrs["sector"] == "marketplace" and not attrs.get("participant_subtype"):
            raise serializers.ValidationError({"participant_subtype": ["Choose buyer, offtaker or OEM."]})
        if attrs["sector"] != "marketplace":
            attrs["participant_subtype"] = ""
        return attrs


# A section is plain answers: text, numbers, yes or no, and lists of text.
MAX_SECTION_KEYS = 60
MAX_TEXT = 2000
MAX_SITES = 100


def _clean_value(value, where):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise serializers.ValidationError(f"{where} is too long.")
        return value
    if isinstance(value, list) and all(isinstance(item, str) and len(item) <= MAX_TEXT for item in value):
        if len(value) > 50:
            raise serializers.ValidationError(f"{where} has too many items.")
        return value
    raise serializers.ValidationError(f"{where} must be text, a number, yes or no, or a list of text.")


def clean_answers(data, where="section"):
    if not isinstance(data, dict):
        raise serializers.ValidationError(f"The {where} must be an object.")
    if len(data) > MAX_SECTION_KEYS:
        raise serializers.ValidationError(f"The {where} has too many answers.")
    cleaned = {}
    for key, value in data.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,59}", key):
            raise serializers.ValidationError(f"{key!r} is not a valid answer key.")
        cleaned[key] = _clean_value(value, key)
    return cleaned


def clean_section(section, data):
    """Check the shape of one section and return it ready to store."""
    if section == "sites":
        if not isinstance(data, list):
            raise serializers.ValidationError("The sites section must be a list.")
        if len(data) > MAX_SITES:
            raise serializers.ValidationError(f"At most {MAX_SITES} entries.")
        rows = []
        for index, row in enumerate(data):
            row = clean_answers(row, f"entry {index + 1}")
            for key, value in row.items():
                if value is not None and not isinstance(value, str):
                    row[key] = str(value)
            rows.append(row)
        return rows
    if section == "declarations":
        cleaned = clean_answers(data, "declarations")
        for key, value in cleaned.items():
            if not isinstance(value, bool):
                raise serializers.ValidationError(f"Declaration {key!r} must be true or false.")
        return cleaned
    return clean_answers(data, section)


class ParticipantDocumentUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    file = serializers.FileField()

    def validate_file(self, value):
        return validate_upload(value)

    def validate_title(self, value):
        application = self.context["application"]
        titles = [title for title, _ in REQUIREMENTS[application.sector]["documents"]]
        if value not in titles:
            raise serializers.ValidationError("This sector does not ask for a document with that title.")
        return value


# Regulator access request

# Free mail providers: a request from one of these needs the agency's letter.
FREE_MAIL = re.compile(r"@(gmail|yahoo|hotmail|outlook|live|icloud|aol|proton(mail)?|ymail)\.", re.IGNORECASE)


class SectorListField(serializers.ListField):
    child = serializers.ChoiceField(choices=SECTOR_SLUGS)

    def to_internal_value(self, data):
        # A multipart form may send the list as one comma separated value.
        if isinstance(data, str):
            data = [data]
        if isinstance(data, list):
            data = [part.strip() for item in data for part in str(item).split(",") if part.strip()]
        values = super().to_internal_value(data)
        return sorted(set(values), key=SECTOR_SLUGS.index)


class RegulatorAccessRequestSerializer(serializers.ModelSerializer):
    requested_sectors = SectorListField(allow_empty=False)
    authority_letter = serializers.FileField(write_only=True, required=False, allow_null=True)
    has_authority_letter = serializers.SerializerMethodField()
    authority_letter_name = serializers.SerializerMethodField()
    requester_email = serializers.EmailField(source="requester.email", read_only=True)
    requester_name = serializers.CharField(source="requester.full_name", read_only=True)

    class Meta:
        model = RegulatorAccessRequest
        fields = [
            "id", "agency_name", "institution_type", "official_email", "office", "state",
            "requester_title", "requested_sectors", "authority_letter", "has_authority_letter",
            "authority_letter_name", "mandate", "status", "approved_sectors", "rejection_reason",
            "decided_at", "organisation", "requester_email", "requester_name", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "approved_sectors", "rejection_reason", "decided_at", "organisation",
            "requester_email", "requester_name", "created_at", "updated_at",
        ]

    def get_has_authority_letter(self, obj) -> bool:
        return bool(obj.authority_letter)

    def get_authority_letter_name(self, obj) -> str:
        return obj.authority_letter.name.rsplit("/", 1)[-1] if obj.authority_letter else ""

    def validate_authority_letter(self, value):
        if value:
            validate_upload(value)
        return value

    def validate(self, attrs):
        if not attrs.get("authority_letter"):
            attrs.pop("authority_letter", None)
        for key in ("agency_name", "office", "state"):
            value = (attrs.get(key) or "").strip()
            if not value or value == "Select…":
                raise serializers.ValidationError({key: ["This field is required."]})
            attrs[key] = value
        if FREE_MAIL.search(attrs.get("official_email", "")) and not attrs.get("authority_letter"):
            raise serializers.ValidationError({
                "authority_letter": ["Attach a letter from your agency, since this is not an official email address."]
            })
        return attrs


class RegulatorApproveSerializer(serializers.Serializer):
    sectors = SectorListField(allow_empty=False)

    def validate_sectors(self, value):
        requested = set(self.context["access_request"].requested_sectors)
        extra = [sector for sector in value if sector not in requested]
        if extra:
            raise serializers.ValidationError(
                f"Only sectors the agency asked for can be approved. Not asked for: {', '.join(extra)}."
            )
        return value


class RegulatorRejectSerializer(serializers.Serializer):
    reason = serializers.CharField()
