from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from accounts.models import User
from accounts.services import issue_email_verification
from common.exceptions import AppError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "phone_number", "email_verified_at", "created_at"]
        read_only_fields = ["id", "email", "email_verified_at", "created_at"]


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["id", "email", "password", "first_name", "last_name", "phone_number"]
        read_only_fields = ["id"]

    def create(self, validated_data):
        with transaction.atomic():
            user = User.objects.create_user(**validated_data)
            issue_email_verification(user)
        return user


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class VerifyEmailSerializer(EmailSerializer):
    code = serializers.RegexField(r"^\d{6}$", max_length=6, min_length=6)


class VerifiedTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        if not self.user.email_verified_at:
            raise AppError(
                "Please verify your email address before signing in.",
                code="email_not_verified",
                status_code=403,
            )
        return data
