from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import PhoneVerificationCode, User


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PhoneVerificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("phone@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.client.force_authenticate(self.user)

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_phone_code_is_hashed_and_single_use(self, generate_code):
        requested = self.client.post(reverse("phone-verification-request"), {"phone_number": "+2348012345678"})
        self.assertEqual(requested.status_code, status.HTTP_200_OK)
        code = PhoneVerificationCode.objects.get()
        self.assertNotEqual(code.code_hash, "123456")
        verified = self.client.post(reverse("phone-verification-confirm"), {"phone_number": "+2348012345678", "code": "123456"})
        self.assertEqual(verified.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.phone_verified_at)
        reused = self.client.post(reverse("phone-verification-confirm"), {"phone_number": "+2348012345678", "code": "123456"})
        self.assertEqual(reused.status_code, status.HTTP_400_BAD_REQUEST)


class SocialLoginDisabledTests(APITestCase):
    """Social login was a second bypass path for the cross-portal login issue
    (it authenticated/created accounts with no portal awareness at all) and
    is disabled until it enforces the same check password login does."""

    def test_social_endpoints_are_not_routed(self):
        self.assertEqual(self.client.post("/api/v1/auth/social/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.post("/api/v1/auth/social/link/").status_code, status.HTTP_404_NOT_FOUND)
