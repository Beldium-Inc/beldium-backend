from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

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
    SocialLoginView,
    SocialLinkView,
)

urlpatterns = [
    path("register/", RegistrationView.as_view(), name="register"),
    path("token/", VerifiedTokenObtainPairView.as_view(), name="token"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
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
    path("social/", SocialLoginView.as_view(), name="social-login"),
    path("social/link/", SocialLinkView.as_view(), name="social-link"),
]
