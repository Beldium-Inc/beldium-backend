from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from accounts.models import AccountAuditEvent, User
from accounts.portal import portal_for_request
from accounts.services import issue_email_verification
from common.exceptions import AppError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "phone_number", "country", "onboarding_role", "email_verified_at", "phone_verified_at", "is_staff", "created_at"]
        # is_staff is what gates the reviewer-only compliance actions, and this
        # endpoint accepts PATCH: leaving it writable would let any account
        # grant itself staff, so it stays read-only.
        read_only_fields = ["id", "email", "email_verified_at", "phone_verified_at", "is_staff", "created_at"]


class AccountAuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountAuditEvent
        fields = ["id", "event_type", "ip_address", "user_agent", "metadata", "created_at"]


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)
    agreed_terms = serializers.BooleanField(write_only=True)
    # The phone OTP endpoint requires E.164, so a number captured in any other
    # shape could never be verified. Optional here, but validated when supplied.
    phone_number = serializers.RegexField(
        r"^\+[1-9]\d{7,14}$",
        max_length=16,
        required=False,
        allow_blank=True,
        error_messages={
            "invalid": "Enter the number in international format, including the country code, e.g. +2348030000000."
        },
    )

    class Meta:
        model = User
        fields = ["id", "email", "password", "confirm_password", "first_name", "last_name", "phone_number", "country", "onboarding_role", "agreed_terms"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        validated_data.pop("confirm_password")
        validated_data.pop("agreed_terms")
        with transaction.atomic():
            user = User.objects.create_user(
                terms_accepted_at=timezone.now(),
                portal=portal_for_request(self.context.get("request")),
                **validated_data,
            )
            issue_email_verification(user)
        return user

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        if not attrs["agreed_terms"]:
            raise serializers.ValidationError({"agreed_terms": ["You must accept the terms to continue."]})
        # Origin-derived, not client-declared (see accounts/portal.py) — an
        # unrecognized origin means this wasn't a real browser call from
        # either known frontend, so refuse rather than create an
        # unrestricted account.
        if not portal_for_request(self.context.get("request")):
            raise serializers.ValidationError({
                "portal": ["Could not determine which Beldium app this request is from."]
            })
        return attrs


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class VerifyEmailSerializer(EmailSerializer):
    code = serializers.RegexField(r"^\d{6}$", max_length=6, min_length=6)


class PhoneVerificationRequestSerializer(serializers.Serializer):
    phone_number = serializers.RegexField(r"^\+[1-9]\d{7,14}$", max_length=16)


class PhoneVerificationConfirmSerializer(PhoneVerificationRequestSerializer):
    code = serializers.RegexField(r"^\d{6}$", max_length=6, min_length=6)


class SocialLoginSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=["google", "microsoft"])
    id_token = serializers.CharField(write_only=True, trim_whitespace=False)


class VerifiedTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Derived from the request's Origin header (see accounts/portal.py),
        # not a client-declared field — a browser can't forge or omit Origin
        # on a cross-origin call the way it could a body field.
        portal = portal_for_request(self.context.get("request"))
        data = super().validate(attrs)
        if not self.user.email_verified_at:
            raise AppError(
                "Please verify your email address before signing in.",
                code="email_not_verified",
                status_code=403,
            )
        if not portal:
            raise AppError(
                "Could not determine which Beldium app this request is from.",
                code="portal_undetermined",
                status_code=403,
            )
        if self.user.portal:
            if self.user.portal != portal:
                raise AppError(
                    "This account belongs to a different Beldium portal.",
                    code="portal_mismatch",
                    status_code=403,
                )
        else:
            # Pre-existing account from before this field existed: lock it to
            # whichever portal it first logs into from now on, rather than
            # leaving it permanently unrestricted.
            self.user.portal = portal
            self.user.save(update_fields=["portal"])
        return data


class PasswordResetConfirmSerializer(VerifyEmailSerializer):
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": ["Passwords do not match."]})
        return attrs


class ChangeEmailRequestSerializer(serializers.Serializer):
    new_email = serializers.EmailField()


class ChangeEmailConfirmSerializer(ChangeEmailRequestSerializer):
    code = serializers.RegexField(r"^\d{6}$", max_length=6, min_length=6)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
