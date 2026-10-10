import os

from django.utils import timezone
from rest_framework import serializers

from careers.models import (
    PARTNER_AGREEMENT_KEYS,
    PARTNER_DOCUMENT_KEYS,
    PARTNER_DOCUMENT_REQUIREMENTS,
    PARTNER_SECTOR_CHOICES,
    Application,
    PartnerApplication,
)

MB = 1024 * 1024
APPLICATION_FILE_MAX_MB = 8
PARTNER_DOCUMENT_MAX_MB = 10

# Leading bytes per extension. These endpoints take files from anyone on the
# internet, so the extension alone is not trusted to say what was uploaded.
FILE_SIGNATURES = {
    "pdf": (b"%PDF",),
    "png": (b"\x89PNG",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "docx": (b"PK\x03\x04",),
    "doc": (b"\xd0\xcf\x11\xe0",),
}
DOCUMENT_EXTENSIONS = ("pdf", "doc", "docx")
IMAGE_EXTENSIONS = ("jpg", "jpeg", "png")
PARTNER_DOCUMENT_EXTENSIONS = ("pdf", "jpg", "jpeg", "png")

MAX_ANSWERS = 40
MAX_ANSWER_LENGTH = 2000


def validate_upload(file, *, extensions, max_mb):
    extension = os.path.splitext(file.name)[1].lstrip(".").lower()
    if extension not in extensions:
        raise serializers.ValidationError(f"Upload one of: {', '.join(extensions)}.")
    if file.size > max_mb * MB:
        raise serializers.ValidationError(f"File exceeds the {max_mb}MB limit.")
    head = file.read(8)
    file.seek(0)
    if not head.startswith(FILE_SIGNATURES[extension]):
        raise serializers.ValidationError("The file's contents do not match its type.")
    return file


class ApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = [
            "reference_id", "pathway", "full_name", "email", "phone", "country", "state", "city",
            "linkedin", "portfolio", "resume", "headshot", "company_profile", "answers",
        ]
        read_only_fields = ["reference_id"]
        extra_kwargs = {
            "resume": {"write_only": True},
            "headshot": {"write_only": True},
            "company_profile": {"write_only": True},
            "answers": {"write_only": True},
        }

    def validate_resume(self, file):
        return validate_upload(file, extensions=DOCUMENT_EXTENSIONS, max_mb=APPLICATION_FILE_MAX_MB)

    def validate_headshot(self, file):
        return file and validate_upload(file, extensions=IMAGE_EXTENSIONS, max_mb=APPLICATION_FILE_MAX_MB)

    def validate_company_profile(self, file):
        return file and validate_upload(file, extensions=DOCUMENT_EXTENSIONS, max_mb=APPLICATION_FILE_MAX_MB)

    def validate_answers(self, answers):
        if not isinstance(answers, dict) or len(answers) > MAX_ANSWERS:
            raise serializers.ValidationError("Answers must be a set of question and answer pairs.")
        for key, value in answers.items():
            if not isinstance(value, (str, int, float, bool)) or len(str(value)) > MAX_ANSWER_LENGTH:
                raise serializers.ValidationError(f"{key}: answer is too long or not text.")
        return answers

    def validate(self, attrs):
        if attrs["pathway"] == Application.Pathway.PARTNERSHIP and not attrs.get("company_profile"):
            raise serializers.ValidationError({"company_profile": "A company profile is required."})
        return attrs


class CompanySerializer(serializers.Serializer):
    # Stored as submitted, so the keys match the form rather than this codebase.
    companyName = serializers.CharField(max_length=200)
    rcNumber = serializers.CharField(max_length=60)
    companyEmail = serializers.EmailField(max_length=200)
    phoneNumber = serializers.CharField(max_length=40)
    businessAddress = serializers.CharField(max_length=400)
    contactPerson = serializers.CharField(max_length=120)


class AgreementSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=PARTNER_AGREEMENT_KEYS)
    signedName = serializers.CharField(max_length=120)


class PartnerApplicationCreateSerializer(serializers.Serializer):
    sector = serializers.ChoiceField(choices=PARTNER_SECTOR_CHOICES, default="logistics")
    company = CompanySerializer()
    agreements = AgreementSerializer(many=True)

    def validate_agreements(self, agreements):
        if sorted(item["key"] for item in agreements) != sorted(PARTNER_AGREEMENT_KEYS):
            raise serializers.ValidationError("Every agreement must be signed exactly once.")
        return agreements

    def create(self, validated_data):
        # The signing time is ours, not the browser's clock.
        signed_at = timezone.now().isoformat()
        return PartnerApplication.objects.create(
            sector=validated_data["sector"],
            company=dict(validated_data["company"]),
            agreements=[{**item, "signedAt": signed_at} for item in validated_data["agreements"]],
        )


class PartnerDocumentUploadSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=PARTNER_DOCUMENT_KEYS)
    file = serializers.FileField()

    def validate_key(self, key):
        application = self.context.get("application")
        if application:
            requirement = PARTNER_DOCUMENT_REQUIREMENTS[application.sector]
            allowed = {*requirement["required"], *requirement["optional"]}
            if key not in allowed:
                raise serializers.ValidationError("This document is not accepted for the selected sector.")
        return key

    def validate_file(self, file):
        return validate_upload(file, extensions=PARTNER_DOCUMENT_EXTENSIONS, max_mb=PARTNER_DOCUMENT_MAX_MB)


class PartnerApplicationStatusSerializer(serializers.ModelSerializer):
    required_documents = serializers.SerializerMethodField()
    optional_documents = serializers.SerializerMethodField()

    class Meta:
        model = PartnerApplication
        fields = ["application_id", "sector", "status", "required_documents", "optional_documents"]

    def get_required_documents(self, application):
        return list(PARTNER_DOCUMENT_REQUIREMENTS[application.sector]["required"])

    def get_optional_documents(self, application):
        return list(PARTNER_DOCUMENT_REQUIREMENTS[application.sector]["optional"])
