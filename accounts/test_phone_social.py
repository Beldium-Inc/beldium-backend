from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import PhoneVerificationCode, SocialIdentity, User


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


class SocialLoginTests(APITestCase):
    @patch("accounts.views.verify_social_token")
    def test_verified_social_identity_creates_and_reuses_account(self, verify_token):
        verify_token.return_value = {"subject": "provider-123", "email": "social@example.com", "name": "Ada Lovelace"}
        first = self.client.post(reverse("social-login"), {"provider": "google", "id_token": "signed-token"})
        second = self.client.post(reverse("social-login"), {"provider": "google", "id_token": "signed-token"})
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(User.objects.filter(email="social@example.com").count(), 1)
        self.assertEqual(SocialIdentity.objects.count(), 1)

    @patch("accounts.views.verify_social_token")
    def test_existing_password_account_requires_authenticated_link(self, verify_token):
        user = User.objects.create_user("existing@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        verify_token.return_value = {"subject": "provider-456", "email": user.email, "name": "Existing User"}
        denied = self.client.post(reverse("social-login"), {"provider": "microsoft", "id_token": "signed-token"})
        self.assertEqual(denied.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(denied.data["error"]["code"], "social_account_link_required")
        self.client.force_authenticate(user)
        linked = self.client.post(reverse("social-link"), {"provider": "microsoft", "id_token": "signed-token"})
        self.assertEqual(linked.status_code, status.HTTP_200_OK)
        self.assertTrue(SocialIdentity.objects.filter(user=user, provider="microsoft").exists())
