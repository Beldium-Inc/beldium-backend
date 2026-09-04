from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.views import (
    CurrentUserView,
    RegistrationView,
    ResendVerificationView,
    VerifiedTokenObtainPairView,
    VerifyEmailView,
)

urlpatterns = [
    path("register/", RegistrationView.as_view(), name="register"),
    path("token/", VerifiedTokenObtainPairView.as_view(), name="token"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("resend-verification/", ResendVerificationView.as_view(), name="resend-verification"),
    path("me/", CurrentUserView.as_view(), name="current-user"),
]
