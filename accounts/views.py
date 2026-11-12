import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import AccountAuditEvent, SocialIdentity, User
from accounts.portal import portal_for_request
from accounts.serializers import (
    SecondFactorCodeSerializer,
    SecondFactorConfirmSerializer,
    SecondFactorEnrolSerializer,
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
from accounts import second_factor
from accounts.refresh_cookie import (
    attach_refresh_cookie,
    clear_refresh_cookie,
    cookie_refresh_token,
    refresh_without_access,
    set_refresh_cookie,
)

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
        if user is not None and second_factor.is_required(user):
            # The password was right, but no tokens leave until the second
            # factor is passed. The pair just made is revoked at once.
            try:
                RefreshToken(response.data["refresh"]).blacklist()
            except (KeyError, TokenError, AttributeError):
                pass
            record_account_event(request, "account.second_factor_challenged", actor=user)
            return Response(second_factor.start_challenge(user), status=status.HTTP_200_OK)
        record_account_event(request, "account.login_succeeded", actor=user)
        return attach_refresh_cookie(response)


class ThrottledTokenRefreshView(TokenRefreshView):
    """Swap a refresh token for a new access token.

    The refresh token comes from the body, as before, or from the HttpOnly
    cookie (G9). A caller presenting an access token must own the refresh
    token. Without a live access token the call works only for the partner
    field roles (Sampler, Inspector, Logistics), whose phones sit idle in the
    field. A cookie token issued within the access token lifetime also works
    without one, so a page reload (the frontend keeps the access token in
    memory only) does not sign anyone out. The cookie is rotated on every
    refresh, so for everyone else an idle session still ends when the access
    token lifetime passes with no activity. A refresh token read from the cookie is rotated: the old one is
    revoked and the new one goes back into the cookie.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [TokenRefreshThrottle]

    def post(self, request, *args, **kwargs):
        body_token = request.data.get("refresh")
        from_cookie = not body_token
        refresh_token = body_token or cookie_refresh_token(request)
        if not refresh_token:
            raise AppError("Refresh token is required.", code="refresh_token_required")
        if from_cookie and not portal_for_request(request):
            # The cookie is sent by the browser on its own, so only the
            # Beldium apps' own origins may use it.
            raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token", status_code=401)
        try:
            token = RefreshToken(refresh_token)
        except TokenError as exc:
            raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token", status_code=401) from exc
        owner_id = str(token.get(api_settings.USER_ID_CLAIM))
        if request.user and request.user.is_authenticated:
            if owner_id != str(request.user.pk):
                raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token", status_code=401)
        else:
            owner = User.objects.filter(pk=owner_id, is_active=True).first()
            if owner is None or not refresh_without_access(owner, token, from_cookie=from_cookie):
                raise AppError(
                    "Authentication credentials were not provided.", code="not_authenticated", status_code=401,
                )
        if not from_cookie:
            return Response({"access": str(token.access_token)}, status=status.HTTP_200_OK)
        user = User.objects.filter(pk=owner_id, is_active=True).first()
        if user is None:
            raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token", status_code=401)
        try:
            token.blacklist()
        except TokenError as exc:
            raise AppError("Invalid or expired refresh token.", code="invalid_refresh_token", status_code=401) from exc
        fresh = RefreshToken.for_user(user)
        response = Response({"access": str(fresh.access_token)}, status=status.HTTP_200_OK)
        set_refresh_cookie(response, str(fresh))
        return response


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
        # This mints a token pair directly, same as /auth/token/ — without
        # this check it was a full bypass of the portal wall: resend a code
        # for any unverified account, verify it from the other app's origin,
        # and sign in there regardless of which portal the account belongs to.
        portal = portal_for_request(request)
        if not portal:
            raise AppError(
                "Could not determine which Beldium app this request is from.",
                code="portal_undetermined", status_code=403,
            )
        if user.portal:
            if user.portal != portal:
                raise AppError(
                    "This account belongs to a different Beldium portal.",
                    code="portal_mismatch", status_code=403,
                )
        else:
            user.portal = portal
            user.save(update_fields=["portal"])
        record_account_event(request, "account.email_verified", actor=user)
        transaction.on_commit(lambda: enqueue_account_email("send_welcome_email", str(user.id)))
        if second_factor.is_required(user):
            return Response({"message": "Email verified successfully.", **second_factor.start_challenge(user)})
        refresh = RefreshToken.for_user(user)
        return attach_refresh_cookie(Response({
            "message": "Email verified successfully.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }, status=status.HTTP_200_OK))


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
        return attach_refresh_cookie(Response({
            "message": "Email address changed successfully.",
            "email": user.email,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }))


class LogoutView(generics.GenericAPIView):
    serializer_class = LogoutSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw = serializer.validated_data.get("refresh") or cookie_refresh_token(request)
        if not raw:
            raise AppError("Refresh token is required.", code="refresh_token_required")
        try:
            token = RefreshToken(raw)
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
        return clear_refresh_cookie(Response(status=status.HTTP_204_NO_CONTENT))


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
    """Disabled: not wired to any URL. Was a second bypass path for the
    cross-portal login issue (creates/authenticates accounts with no portal
    awareness). Re-enable only once it takes and enforces a portal field the
    way VerifiedTokenObtainPairSerializer does."""

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


# --- second factor at sign in (G8) --------------------------------------------


def _token_pair(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


class SecondFactorTokenView(generics.GenericAPIView):
    """Swap a sign in challenge and a code from the authenticator app (or a
    recovery code) for a token pair. Five wrong codes lock the challenge."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerificationAttemptThrottle]
    serializer_class = SecondFactorCodeSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            challenge = second_factor.open_challenge(data["challenge_token"])
            user = challenge.user
            passed = second_factor.check_code(user, data["code"])
            if passed:
                second_factor.close_challenge(challenge)
            else:
                second_factor.fail_challenge(challenge)
        if not passed:
            record_account_event(request, "account.second_factor_failed", actor=user)
            raise AppError("That code is not correct.", code="second_factor_invalid")
        record_account_event(request, "account.login_succeeded", actor=user, second_factor=True)
        return attach_refresh_cookie(Response(_token_pair(user)))


def _enrolling_user(request, challenge_token):
    """The signed in caller, or the owner of a valid sign in challenge."""
    if challenge_token:
        return second_factor.open_challenge(challenge_token)
    if request.user and request.user.is_authenticated:
        return None
    raise AppError("Sign in again. The code step took too long.", code="challenge_expired", status_code=401)


class SecondFactorEnrolView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    throttle_classes = [VerificationAttemptThrottle]
    serializer_class = SecondFactorEnrolSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            challenge = _enrolling_user(request, serializer.validated_data.get("challenge_token"))
            user = challenge.user if challenge else request.user
            result = second_factor.begin_enrolment(user)
        record_account_event(request, "account.second_factor_enrolment_started", actor=user)
        return Response(result)


class SecondFactorEnrolConfirmView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    throttle_classes = [VerificationAttemptThrottle]
    serializer_class = SecondFactorConfirmSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            challenge = _enrolling_user(request, data.get("challenge_token"))
            user = challenge.user if challenge else request.user
            codes = second_factor.confirm_enrolment(user, data["code"])
            if codes is None and challenge:
                second_factor.fail_challenge(challenge)
            elif codes is not None and challenge:
                second_factor.close_challenge(challenge)
        if codes is None:
            record_account_event(request, "account.second_factor_failed", actor=user)
            raise AppError("That code is not correct.", code="second_factor_invalid")
        record_account_event(request, "account.second_factor_enrolled", actor=user)
        body = {"recovery_codes": codes}
        if challenge:
            body.update(_token_pair(user))
        return attach_refresh_cookie(Response(body))
