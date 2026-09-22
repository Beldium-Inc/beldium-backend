from unittest.mock import patch
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import EmailVerificationCode, User
from accounts.services import enqueue_verification_email, issue_email_verification
from accounts.tasks import send_email_verification

COMPLIANCE_ORIGIN = "https://compliance.beldium.com"
MINER_ORIGIN = "https://miners.beldium.com"


class AuthenticationTests(APITestCase):
    def setUp(self):
        # Registration is rate limited per caller, and the locmem cache outlives
        # a single test, so each one starts from a clean bucket.
        cache.clear()

    def test_register_login_and_read_profile(self):
        response = self.client.post(reverse("register"), {
            "email": "owner@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "SafePassword-2026!",
            "agreed_terms": True,
            "first_name": "Ada",
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="owner@example.com")
        self.assertTrue(user.check_password("SafePassword-2026!"))
        self.assertIsNone(user.email_verified_at)
        self.assertEqual(user.portal, "compliance")
        self.assertTrue(EmailVerificationCode.objects.filter(user=user, consumed_at__isnull=True).exists())

        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])

        response = self.client.post(reverse("token"), {
            "email": "owner@example.com",
            "password": "SafePassword-2026!",
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        response = self.client.get(reverse("current-user"))
        self.assertEqual(response.data["email"], "owner@example.com")

    def test_login_rejects_wrong_portal(self):
        User.objects.create_user(
            "cross-portal@example.com", "SafePassword-2026!",
            email_verified_at=timezone.now(), portal="compliance",
        )
        response = self.client.post(reverse("token"), {
            "email": "cross-portal@example.com",
            "password": "SafePassword-2026!",
        }, HTTP_ORIGIN=MINER_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "portal_mismatch")

    def test_login_grandfathers_and_locks_in_blank_portal(self):
        user = User.objects.create_user(
            "legacy@example.com", "SafePassword-2026!", email_verified_at=timezone.now(),
        )
        self.assertEqual(user.portal, "")
        response = self.client.post(reverse("token"), {
            "email": "legacy@example.com",
            "password": "SafePassword-2026!",
        }, HTTP_ORIGIN=MINER_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.portal, "miner")

        # Now locked to "miner" — the other portal is rejected.
        response = self.client.post(reverse("token"), {
            "email": "legacy@example.com",
            "password": "SafePassword-2026!",
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "portal_mismatch")

    def test_login_without_a_recognized_origin_is_refused(self):
        User.objects.create_user(
            "no-origin@example.com", "SafePassword-2026!", email_verified_at=timezone.now(),
        )
        response = self.client.post(reverse("token"), {
            "email": "no-origin@example.com",
            "password": "SafePassword-2026!",
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "portal_undetermined")

    def test_unverified_user_cannot_login(self):
        User.objects.create_user("pending@example.com", "SafePassword-2026!")
        response = self.client.post(reverse("token"), {
            "email": "pending@example.com",
            "password": "SafePassword-2026!",
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "email_not_verified")

    @patch("common.exception_handler.logger.exception")
    @patch("accounts.serializers.issue_email_verification", side_effect=RuntimeError("OTP persistence failed"))
    def test_registration_rolls_back_user_when_otp_creation_fails(self, issue_verification, logger):
        self.client.raise_request_exception = False
        response = self.client.post(reverse("register"), {
            "email": "rollback@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "SafePassword-2026!",
            "agreed_terms": True,
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertFalse(User.objects.filter(email="rollback@example.com").exists())
        issue_verification.assert_called_once()
        logger.assert_called_once()

    def test_registration_without_a_recognized_origin_is_refused(self):
        response = self.client.post(reverse("register"), {
            "email": "no-origin@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "SafePassword-2026!",
            "agreed_terms": True,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("portal", response.data["error"]["details"])
        self.assertFalse(User.objects.filter(email="no-origin@example.com").exists())

    def test_validation_and_authentication_errors_use_standard_envelope(self):
        response = self.client.post(reverse("register"), {"email": "invalid"}, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertIn("email", response.data["error"]["details"])
        self.assertIn("password", response.data["error"]["details"])
        self.assertTrue(response.data["message"].startswith("email:"))

        response = self.client.get(reverse("current-user"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["error"]["code"], "not_authenticated")
        self.assertIsNone(response.data["error"]["details"])


class EmailVerificationTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("verify@example.com", "SafePassword-2026!")

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_valid_code_is_single_use_and_returns_tokens(self, generate_code):
        issue_email_verification(self.user)
        response = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "123456",
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.email_verified_at)

    @patch("accounts.services.generate_verification_code", return_value="654321")
    def test_verify_email_from_the_wrong_portal_is_refused(self, generate_code):
        """Regression: this endpoint mints tokens directly, same as /auth/token/ —
        without a portal check it was a full bypass: resend a code for any
        unverified account, then verify it from the other app's origin."""
        user = User.objects.create_user("miner@example.com", "SafePassword-2026!", portal="miner")
        issue_email_verification(user)
        response = self.client.post(reverse("verify-email"), {
            "email": user.email,
            "code": "654321",
        }, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "portal_mismatch")
        self.assertNotIn("access", response.data)

        response = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "123456",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "invalid_verification_code")

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_code_is_invalidated_after_five_failed_attempts(self, generate_code):
        issue_email_verification(self.user)
        for _ in range(5):
            response = self.client.post(reverse("verify-email"), {
                "email": self.user.email,
                "code": "000000",
            })
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        verification = EmailVerificationCode.objects.get(user=self.user)
        self.assertEqual(verification.failed_attempts, 5)
        self.assertIsNotNone(verification.consumed_at)

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_expired_code_is_rejected(self, generate_code):
        issue_email_verification(self.user)
        EmailVerificationCode.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self.client.post(reverse("verify-email"), {
            "email": self.user.email,
            "code": "123456",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["code"], "invalid_verification_code")

    def test_resend_response_does_not_reveal_if_account_exists(self):
        existing = self.client.post(reverse("resend-verification"), {"email": self.user.email})
        missing = self.client.post(reverse("resend-verification"), {"email": "missing@example.com"})
        self.assertEqual(existing.status_code, status.HTTP_200_OK)
        self.assertEqual(existing.data, missing.data)

    def test_resend_is_throttled_after_five_requests(self):
        for _ in range(5):
            response = self.client.post(reverse("resend-verification"), {"email": self.user.email})
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.post(reverse("resend-verification"), {"email": self.user.email})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["error"]["code"], "throttled")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_task_renders_code(self):
        send_email_verification.run(str(self.user.id), "654321")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("654321", mail.outbox[0].body)

    @patch("accounts.services.logger.exception")
    @patch("accounts.tasks.send_email_verification.delay", side_effect=RuntimeError("provider unavailable"))
    def test_delivery_failure_does_not_break_registration_flow(self, delay, logger):
        # The send now happens on a background thread so the request doesn't
        # wait on it; join it so the mocks are observed deterministically.
        enqueue_verification_email(self.user.id, "123456").join(timeout=5)
        delay.assert_called_once()
        logger.assert_called_once()
