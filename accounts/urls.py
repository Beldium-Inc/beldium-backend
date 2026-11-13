from django.urls import path
from accounts.views import (
    AccountAuditListView,
    ChangeEmailConfirmView,
    ChangeEmailRequestView,
    ChangePasswordView,
    CurrentUserView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegistrationView,
    ResendVerificationView,
    VerifiedTokenObtainPairView,
    VerifyEmailView,
    PhoneVerificationRequestView,
    PhoneVerificationConfirmView,
    ThrottledTokenRefreshView,
    MfaBackupCodesView,
    MfaConfirmView,
    MfaDisableView,
    MfaSetupView,
    MfaStatusView,
    MfaVerifyView,
)

urlpatterns = [
    path("register/", RegistrationView.as_view(), name="register"),
    path("token/", VerifiedTokenObtainPairView.as_view(), name="token"),
    path("token/refresh/", ThrottledTokenRefreshView.as_view(), name="token-refresh"),
    path("verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("resend-verification/", ResendVerificationView.as_view(), name="resend-verification"),
    path("password-reset/request/", PasswordResetRequestView.as_view(), name="password-reset-request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("change-password/", ChangePasswordView.as_view(), name="change-password"),
    path("change-email/request/", ChangeEmailRequestView.as_view(), name="change-email-request"),
    path("change-email/confirm/", ChangeEmailConfirmView.as_view(), name="change-email-confirm"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("audit/", AccountAuditListView.as_view(), name="account-audit"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("verify-phone/request/", PhoneVerificationRequestView.as_view(), name="phone-verification-request"),
    path("verify-phone/confirm/", PhoneVerificationConfirmView.as_view(), name="phone-verification-confirm"),
    path("mfa/", MfaStatusView.as_view(), name="mfa-status"),
    path("mfa/setup/", MfaSetupView.as_view(), name="mfa-setup"),
    path("mfa/confirm/", MfaConfirmView.as_view(), name="mfa-confirm"),
    path("mfa/disable/", MfaDisableView.as_view(), name="mfa-disable"),
    path("mfa/backup-codes/", MfaBackupCodesView.as_view(), name="mfa-backup-codes"),
    path("mfa/verify/", MfaVerifyView.as_view(), name="mfa-verify"),
    # Social login (Google/Microsoft) is disabled: it was a second bypass path
    # for the cross-portal login issue (creates/authenticates accounts with no
    # portal awareness at all). Re-enable only once it enforces the same
    # portal check as VerifiedTokenObtainPairSerializer.
]
