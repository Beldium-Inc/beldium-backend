import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import AccountAuditEvent, SocialIdentity, User
from accounts.serializers import (
    AccountAuditEventSerializer,
    ChangeEmailConfirmSerializer,
    ChangeEmailRequestSerializer,
    ChangePasswordSerializer,
    EmailSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    RegistrationSerializer,
    UserSerializer,
    VerifiedTokenObtainPairSerializer,
    VerifyEmailSerializer,
    PhoneVerificationRequestSerializer,
    PhoneVerificationConfirmSerializer,
    SocialLoginSerializer,
)
from accounts.services import (
    blacklist_user_refresh_tokens,
    confirm_email_change,
    enqueue_account_email,
    issue_email_change,
    issue_email_verification,
    issue_password_reset,
    reset_password,
    verify_email_code,
    issue_phone_verification,
    verify_phone_code,
)
from accounts.throttles import (
    LoginEmailThrottle,
    LoginThrottle,
    RegistrationThrottle,
    SensitiveActionThrottle,
    SocialAuthThrottle,
    TokenRefreshThrottle,
    VerificationAttemptThrottle,
    VerificationIssueThrottle,
)
from common.exceptions import AppError
from accounts.audit import record_account_event
from accounts.social import verify_social_token

logger = logging.getLogger(__name__)


class RegistrationView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [RegistrationThrottle]

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data = {
            "message": "Registration successful. Check your email for a verification code.",
            "user": response.data,
        }
        user = User.objects.get(id=response.data["user"]["id"])
        record_account_event(request, "account.registered", actor=user)
        return response


class CurrentUserView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class VerifiedTokenObtainPairView(TokenObtainPairView):
    serializer_class = VerifiedTokenObtainPairSerializer
    # Password guessing is bounded by nothing else: there is no lockout, and
    # the per-code budgets in accounts.services cover OTPs rather than
    # passwords. The pair covers one caller grinding an account and a pool of
    # callers grinding the same one.
    throttle_classes = [LoginThrottle, LoginEmailThrottle]

    def post(self, request, *args, **kwargs):
        user = User.objects.filter(email__iexact=request.data.get("email", "")).first()
        try:
            response = super().post(request, *args, **kwargs)
        except Exception:
            record_account_event(request, "account.login_failed", actor=user)
            raise
        record_account_event(request, "account.login_succeeded", actor=user)
        return response


class ThrottledTokenRefreshView(TokenRefreshView):
    """Refresh mints access tokens, so it needs a ceiling like any other credential endpoint."""

    throttle_classes = [TokenRefreshThrottle]


class ResendVerificationView(generics.GenericAPIView):
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerificationIssueThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        logger.info(
            "resend-verification for %s: user_found=%s already_verified=%s",
            email, bool(user), bool(user and user.email_verified_at),
        )
        if user and not user.email_verified_at:
            issue_email_verification(user)
        return Response({
            "message": "If an unverified account exists for that email, a verification code has been sent."
        })


class VerifyEmailView(generics.GenericAPIView):
    serializer_class = VerifyEmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerificationAttemptThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_email_code(
            serializer.validated_data["email"],
            serializer.validated_data["code"],
        )
        refresh = RefreshToken.for_user(user)
        record_account_event(request, "account.email_verified", actor=user)
        transaction.on_commit(lambda: enqueue_account_email("send_welcome_email", str(user.id)))
        return Response({
            "message": "Email verified successfully.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }, status=status.HTTP_200_OK)


class PasswordResetRequestView(generics.GenericAPIView):
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerificationIssueThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue_password_reset(serializer.validated_data["email"])
        return Response({"message": "If an account exists for that email, a password reset code has been sent."})


class PasswordResetConfirmView(generics.GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerificationAttemptThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = reset_password(
            serializer.validated_data["email"],
            serializer.validated_data["code"],
            serializer.validated_data["new_password"],
        )
        record_account_event(request, "account.password_reset", actor=user)
        return Response({"message": "Password reset successfully. You can now sign in."})


class ChangePasswordView(generics.GenericAPIView):
    serializer_class = ChangePasswordSerializer
    # This endpoint checks current_password, so it is a password oracle for a
    # stolen access token and needs the same ceiling as signing in.
    throttle_classes = [SensitiveActionThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            raise AppError("Current password is incorrect.", code="incorrect_password")
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])
        blacklist_user_refresh_tokens(request.user)
        record_account_event(request, "account.password_changed")
        return Response({"message": "Password changed successfully."})


class ChangeEmailRequestView(generics.GenericAPIView):
    serializer_class = ChangeEmailRequestSerializer
    throttle_classes = [VerificationIssueThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue_email_change(request.user, serializer.validated_data["new_email"])
        record_account_event(request, "account.email_change_requested")
        return Response({"message": "A verification code has been sent to the new email address."})


class ChangeEmailConfirmView(generics.GenericAPIView):
    serializer_class = ChangeEmailConfirmSerializer
    throttle_classes = [VerificationAttemptThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_email = request.user.email
        user = confirm_email_change(
            request.user,
            serializer.validated_data["new_email"],
            serializer.validated_data["code"],
        )
        record_account_event(request, "account.email_changed", previous_email=old_email)
        # confirm_email_change revokes every refresh token, because the account
        # just changed the identity that recovers it. Reissue here so the
        # caller who did the change stays signed in.
        refresh = RefreshToken.for_user(user)
        return Response({
            "message": "Email address changed successfully.",
            "email": user.email,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        })


class LogoutView(generics.GenericAPIView):
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
        except TokenError as exc:
            raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token") from exc
        # Without this check the endpoint revokes any well-formed token that is
        # presented to it, which turns a leaked refresh token into a way to end
        # somebody else's session. Same error either way: whether a token is
        # valid-but-another-account's is not the caller's business.
        if str(token.get(api_settings.USER_ID_CLAIM)) != str(request.user.pk):
            raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token")
        token.blacklist()
        record_account_event(request, "account.logged_out")
        return Response(status=status.HTTP_204_NO_CONTENT)


class AccountAuditListView(generics.ListAPIView):
    queryset = AccountAuditEvent.objects.none()
    serializer_class = AccountAuditEventSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        return self.request.user.account_audit_events.all()


class PhoneVerificationRequestView(generics.GenericAPIView):
    serializer_class = PhoneVerificationRequestSerializer
    throttle_classes = [VerificationIssueThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue_phone_verification(request.user, serializer.validated_data["phone_number"])
        return Response({"message": "A verification code has been sent to your phone."})


class PhoneVerificationConfirmView(generics.GenericAPIView):
    serializer_class = PhoneVerificationConfirmSerializer
    throttle_classes = [VerificationAttemptThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = verify_phone_code(request.user, serializer.validated_data["phone_number"], serializer.validated_data["code"])
        record_account_event(request, "account.phone_verified")
        return Response({"message": "Phone number verified successfully.", "phone_number": user.phone_number})


class SocialLoginView(generics.GenericAPIView):
    serializer_class = SocialLoginSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [SocialAuthThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data["provider"]
        identity = verify_social_token(provider, serializer.validated_data["id_token"])
        social = SocialIdentity.objects.select_related("user").filter(provider=provider, subject=identity["subject"]).first()
        if social:
            user = social.user
        else:
            email = User.objects.normalize_email(identity["email"]).lower()
            user = User.objects.filter(email__iexact=email).first()
            if user:
                raise AppError(
                    "Sign in normally, then link this provider from your account settings.",
                    code="social_account_link_required", status_code=409,
                )
            names = identity.get("name", "").strip().split(maxsplit=1)
            user = User.objects.create_user(
                email=email, password=None, email_verified_at=timezone.now(),
                first_name=names[0] if names else "", last_name=names[1] if len(names) > 1 else "",
            )
            SocialIdentity.objects.create(user=user, provider=provider, subject=identity["subject"])
        if not user.is_active:
            raise AppError("This account is inactive.", code="account_inactive", status_code=403)
        refresh = RefreshToken.for_user(user)
        record_account_event(request, "account.social_login_succeeded", actor=user, provider=provider)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data})


class SocialLinkView(generics.GenericAPIView):
    serializer_class = SocialLoginSerializer

    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data["provider"]
        identity = verify_social_token(provider, serializer.validated_data["id_token"])
        if identity["email"].strip().lower() != request.user.email.lower():
            raise AppError("The provider email must match your Beldium account email.", code="social_email_mismatch", status_code=409)
        existing = SocialIdentity.objects.filter(provider=provider, subject=identity["subject"]).first()
        if existing and existing.user_id != request.user.id:
            raise AppError("This provider identity is linked to another account.", code="social_identity_in_use", status_code=409)
        SocialIdentity.objects.get_or_create(user=request.user, provider=provider, subject=identity["subject"])
        record_account_event(request, "account.social_identity_linked", provider=provider)
        return Response({"message": f"{provider.title()} account linked successfully."})
