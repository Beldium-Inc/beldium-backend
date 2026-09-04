from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.models import User
from accounts.serializers import (
    EmailSerializer,
    RegistrationSerializer,
    UserSerializer,
    VerifiedTokenObtainPairSerializer,
    VerifyEmailSerializer,
)
from accounts.services import issue_email_verification, verify_email_code
from accounts.throttles import VerificationAttemptThrottle, VerificationIssueThrottle


class RegistrationView(generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        response.data = {
            "message": "Registration successful. Check your email for a verification code.",
            "user": response.data,
        }
        return response


class CurrentUserView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class VerifiedTokenObtainPairView(TokenObtainPairView):
    serializer_class = VerifiedTokenObtainPairSerializer


class ResendVerificationView(generics.GenericAPIView):
    serializer_class = EmailSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VerificationIssueThrottle]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email__iexact=serializer.validated_data["email"]).first()
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
        return Response({
            "message": "Email verified successfully.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }, status=status.HTTP_200_OK)
