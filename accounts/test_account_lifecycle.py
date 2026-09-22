from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import AccountAuditEvent, AccountRecoveryCode, User
from accounts.services import issue_email_change, issue_password_reset


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class RegistrationContractTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_registration_requires_matching_passwords_and_terms(self):
        mismatch = self.client.post(reverse("register"), {
            "email": "user@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "DifferentPassword-2026!",
            "agreed_terms": True,
            "portal": "compliance",
        })
        self.assertEqual(mismatch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm_password", mismatch.data["error"]["details"])

        no_terms = self.client.post(reverse("register"), {
            "email": "user@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "SafePassword-2026!",
            "agreed_terms": False,
            "portal": "compliance",
        })
        self.assertEqual(no_terms.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.exists())

    def test_registration_requires_a_portal(self):
        response = self.client.post(reverse("register"), {
            "email": "user@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "SafePassword-2026!",
            "agreed_terms": True,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("portal", response.data["error"]["details"])
        self.assertFalse(User.objects.exists())

    def test_registration_records_terms_and_audit_event(self):
        response = self.client.post(reverse("register"), {
            "email": "user@example.com",
            "password": "SafePassword-2026!",
            "confirm_password": "SafePassword-2026!",
            "agreed_terms": True,
            "country": "Nigeria",
            "portal": "miner",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get()
        self.assertIsNotNone(user.terms_accepted_at)
        self.assertEqual(user.country, "Nigeria")
        self.assertTrue(AccountAuditEvent.objects.filter(actor=user, event_type="account.registered").exists())


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PasswordLifecycleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            "user@example.com", "OldPassword-2026!", email_verified_at=timezone.now()
        )

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_password_reset_is_generic_single_use_and_audited(self, generate_code):
        known = self.client.post(reverse("password-reset-request"), {"email": self.user.email})
        unknown = self.client.post(reverse("password-reset-request"), {"email": "missing@example.com"})
        self.assertEqual(known.data, unknown.data)
        code = AccountRecoveryCode.objects.get(user=self.user)
        self.assertNotEqual(code.code_hash, "123456")

        response = self.client.post(reverse("password-reset-confirm"), {
            "email": self.user.email,
            "code": "123456",
            "new_password": "NewPassword-2026!",
            "confirm_password": "NewPassword-2026!",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPassword-2026!"))
        self.assertTrue(AccountAuditEvent.objects.filter(actor=self.user, event_type="account.password_reset").exists())

        reused = self.client.post(reverse("password-reset-confirm"), {
            "email": self.user.email,
            "code": "123456",
            "new_password": "ThirdPassword-2026!",
            "confirm_password": "ThirdPassword-2026!",
        })
        self.assertEqual(reused.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authenticated_password_change_requires_current_password(self):
        self.client.force_authenticate(self.user)
        denied = self.client.post(reverse("change-password"), {
            "current_password": "wrong",
            "new_password": "NewPassword-2026!",
            "confirm_password": "NewPassword-2026!",
        })
        self.assertEqual(denied.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(denied.data["error"]["code"], "incorrect_password")

        allowed = self.client.post(reverse("change-password"), {
            "current_password": "OldPassword-2026!",
            "new_password": "NewPassword-2026!",
            "confirm_password": "NewPassword-2026!",
        })
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPassword-2026!"))

    def test_logout_blacklists_refresh_token(self):
        refresh = RefreshToken.for_user(self.user)
        self.client.force_authenticate(self.user)
        response = self.client.post(reverse("logout"), {"refresh": str(refresh)})
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_password_reset_revokes_existing_refresh_tokens(self, generate_code):
        refresh = RefreshToken.for_user(self.user)
        issue_password_reset(self.user.email)
        response = self.client.post(reverse("password-reset-confirm"), {
            "email": self.user.email,
            "code": "123456",
            "new_password": "NewPassword-2026!",
            "confirm_password": "NewPassword-2026!",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())

    def test_login_events_and_personal_audit_endpoint(self):
        failed = self.client.post(reverse("token"), {
            "email": self.user.email,
            "password": "wrong",
            "portal": "compliance",
        })
        self.assertEqual(failed.status_code, status.HTTP_401_UNAUTHORIZED)
        succeeded = self.client.post(reverse("token"), {
            "email": self.user.email,
            "password": "OldPassword-2026!",
            "portal": "compliance",
        })
        self.assertEqual(succeeded.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.user)
        audit = self.client.get(reverse("account-audit"))
        self.assertEqual(audit.status_code, status.HTTP_200_OK)
        event_types = {event["event_type"] for event in audit.data["results"]}
        self.assertIn("account.login_failed", event_types)
        self.assertIn("account.login_succeeded", event_types)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EmailChangeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            "old@example.com", "SafePassword-2026!", email_verified_at=timezone.now()
        )
        self.client.force_authenticate(self.user)

    @patch("accounts.services.generate_verification_code", return_value="654321")
    def test_email_changes_only_after_new_address_is_verified(self, generate_code):
        requested = self.client.post(reverse("change-email-request"), {"new_email": "new@example.com"})
        self.assertEqual(requested.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")

        confirmed = self.client.post(reverse("change-email-confirm"), {
            "new_email": "new@example.com",
            "code": "654321",
        })
        self.assertEqual(confirmed.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertTrue(AccountAuditEvent.objects.filter(actor=self.user, event_type="account.email_changed").exists())

    def test_email_change_rejects_address_owned_by_another_user(self):
        User.objects.create_user("taken@example.com", "SafePassword-2026!")
        response = self.client.post(reverse("change-email-request"), {"new_email": "taken@example.com"})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "email_unavailable")
