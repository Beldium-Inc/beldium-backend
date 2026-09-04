from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import EmailVerificationCode, User
from accounts.services import issue_email_verification


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AuthenticationEdgeCaseTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_registration_normalizes_email_and_never_returns_password(self):
        response = self.client.post(reverse("register"), {
            "email": "Owner@EXAMPLE.COM",
            "password": "SafePassword-2026!",
            "first_name": "Ada",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("password", response.data["user"])
        self.assertEqual(User.objects.get().email, "owner@example.com")

    def test_duplicate_registration_has_standard_validation_error(self):
        User.objects.create_user("owner@example.com", "SafePassword-2026!")

        response = self.client.post(reverse("register"), {
            "email": "owner@example.com",
            "password": "AnotherPassword-2026!",
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertIn("email", response.data["error"]["details"])
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_is_rejected_without_creating_user_or_code(self):
        response = self.client.post(reverse("register"), {
            "email": "weak@example.com",
            "password": "12345678",
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())
        self.assertEqual(EmailVerificationCode.objects.count(), 0)

    def test_wrong_password_uses_generic_authentication_error(self):
        User.objects.create_user(
            "verified@example.com",
            "SafePassword-2026!",
            email_verified_at=timezone.now(),
        )

        response = self.client.post(reverse("token"), {
            "email": "verified@example.com",
            "password": "WrongPassword-2026!",
        })

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["error"]["code"], "authentication_failed")
        self.assertNotIn("verified@example.com", response.data["message"])

    def test_profile_update_cannot_change_email_or_verification_timestamp(self):
        original_verified_at = timezone.now()
        user = User.objects.create_user(
            "fixed@example.com",
            "SafePassword-2026!",
            email_verified_at=original_verified_at,
        )
        self.client.force_authenticate(user)

        response = self.client.patch(reverse("current-user"), {
            "email": "attacker@example.com",
            "email_verified_at": None,
            "first_name": "Updated",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.email, "fixed@example.com")
        self.assertEqual(user.email_verified_at, original_verified_at)
        self.assertEqual(user.first_name, "Updated")

    def test_refresh_token_returns_new_access_token(self):
        user = User.objects.create_user(
            "verified@example.com",
            "SafePassword-2026!",
            email_verified_at=timezone.now(),
        )
        login = self.client.post(reverse("token"), {
            "email": user.email,
            "password": "SafePassword-2026!",
        })

        response = self.client.post(reverse("token-refresh"), {"refresh": login.data["refresh"]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EmailVerificationEdgeCaseTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("verify@example.com", "SafePassword-2026!")

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_plaintext_code_is_never_stored(self, generate_code):
        issue_email_verification(self.user)

        verification = EmailVerificationCode.objects.get(user=self.user)
        self.assertNotEqual(verification.code_hash, "123456")
        self.assertNotIn("123456", verification.code_hash)

    @patch("accounts.services.generate_verification_code", side_effect=["111111", "222222"])
    def test_resend_invalidates_previous_code(self, generate_code):
        issue_email_verification(self.user)
        first = EmailVerificationCode.objects.get(user=self.user)
        issue_email_verification(self.user)
        first.refresh_from_db()

        self.assertIsNotNone(first.consumed_at)
        old_response = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "111111",
        })
        self.assertEqual(old_response.status_code, status.HTTP_400_BAD_REQUEST)

        new_response = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "222222",
        })
        self.assertEqual(new_response.status_code, status.HTTP_200_OK)

    def test_resend_for_verified_user_does_not_create_code(self):
        self.user.email_verified_at = timezone.now()
        self.user.save(update_fields=["email_verified_at"])

        response = self.client.post(reverse("resend-verification"), {"email": self.user.email})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(EmailVerificationCode.objects.count(), 0)

    def test_malformed_code_uses_validation_envelope(self):
        response = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "12ab",
        })

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertIn("code", response.data["error"]["details"])

    def test_unknown_email_and_wrong_code_have_same_public_error(self):
        issue_email_verification(self.user)
        wrong = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "000000",
        })
        unknown = self.client.post(reverse("verify-email"), {
            "email": "missing@example.com",
            "code": "000000",
        })

        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(wrong.data, unknown.data)
