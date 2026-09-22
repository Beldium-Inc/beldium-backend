"""Regression tests for the auth audit findings.

Each test here reproduces an attack that worked before the fix, so a
regression shows up as the attack succeeding again rather than as a
coverage gap.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import AccountRecoveryCode, EmailVerificationCode, User

STRONG_PASSWORD = "CorrectHorse!9times"


def _rest_framework_with(**overrides):
    from django.conf import settings

    merged = dict(settings.REST_FRAMEWORK)
    merged.update(overrides)
    return merged


class ForwardedHeaderTrustTests(TestCase):
    """X-Forwarded-For is client-written, so it may only be trusted hop by hop."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _register(self, index, **extra):
        return self.client.post(
            reverse("register"),
            {
                "email": f"caller{index}@example.com",
                "password": STRONG_PASSWORD,
                "confirm_password": STRONG_PASSWORD,
                "agreed_terms": True,
                "portal": "compliance",
            },
            format="json",
            **extra,
        )

    def test_rotating_the_header_no_longer_buys_a_fresh_throttle_bucket(self):
        statuses = [
            self._register(index, HTTP_X_FORWARDED_FOR=f"10.0.0.{index}").status_code
            for index in range(30)
        ]
        self.assertIn(429, statuses, "registration throttle was bypassed by rotating X-Forwarded-For")
        self.assertEqual(statuses.count(201), 10)

    def test_audit_trail_records_the_peer_not_the_claimed_address(self):
        self._register(0, HTTP_X_FORWARDED_FOR="203.0.113.9")
        user = User.objects.get(email="caller0@example.com")
        event = user.account_audit_events.get(event_type="account.registered")
        self.assertNotEqual(event.ip_address, "203.0.113.9")

    def test_one_declared_proxy_reads_the_hop_that_proxy_wrote(self):
        with override_settings(REST_FRAMEWORK=_rest_framework_with(NUM_PROXIES=1)):
            self._register(1, HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.4")
        user = User.objects.get(email="caller1@example.com")
        event = user.account_audit_events.get(event_type="account.registered")
        self.assertEqual(event.ip_address, "198.51.100.4")


class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(email="victim@example.com", password=STRONG_PASSWORD)
        self.user.email_verified_at = timezone.now()
        self.user.save()

    def test_password_guessing_is_stopped(self):
        statuses = [
            self.client.post(
                reverse("token"),
                {"email": "victim@example.com", "password": f"wrong{attempt}", "portal": "compliance"},
                format="json",
            ).status_code
            for attempt in range(30)
        ]
        self.assertIn(429, statuses, "sign-in accepted unlimited password guesses")
        self.assertLessEqual(statuses.count(401), 10)

    def test_a_distributed_attack_on_one_account_is_also_bounded(self):
        """Varying the source address must not hand the same account a fresh budget."""
        with override_settings(REST_FRAMEWORK=_rest_framework_with(NUM_PROXIES=1)):
            statuses = [
                self.client.post(
                    reverse("token"),
                    {"email": "victim@example.com", "password": f"wrong{attempt}", "portal": "compliance"},
                    format="json",
                    HTTP_X_FORWARDED_FOR=f"198.51.100.{attempt}",
                ).status_code
                for attempt in range(40)
            ]
        self.assertIn(429, statuses)
        self.assertLessEqual(statuses.count(401), 20)


class RecoveryCodeBudgetTests(TestCase):
    """A per-code attempt cap is meaningless if a new code resets it."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(email="victim@example.com", password=STRONG_PASSWORD)
        self.user.email_verified_at = timezone.now()
        self.user.save()

    @patch("accounts.services.generate_verification_code", return_value="424242")
    def test_rotating_reset_codes_cannot_walk_the_six_digit_space(self, _code):
        guesses = 0
        for issue in range(40):
            self.client.post(
                reverse("password-reset-request"),
                {"email": "victim@example.com"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"10.1.{issue}.7",
            )
            for attempt in range(5):
                guesses += 1
                response = self.client.post(
                    reverse("password-reset-confirm"),
                    {
                        "email": "victim@example.com",
                        "code": f"{guesses:06d}",
                        "new_password": "AttackerOwns!2026",
                        "confirm_password": "AttackerOwns!2026",
                    },
                    format="json",
                    HTTP_X_FORWARDED_FOR=f"10.1.{issue}.{attempt}",
                )
                self.assertNotEqual(response.status_code, 200)
                if response.status_code == 429:
                    break
            else:
                continue
            break

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG_PASSWORD), "the account was taken over")
        self.assertFalse(self.user.check_password("AttackerOwns!2026"))
        # Ten failures per hour is the budget; the throttle stops it sooner still.
        self.assertLessEqual(guesses, 25, f"{guesses} guesses got through")

    @patch("accounts.services.generate_verification_code", return_value="424242")
    def test_the_real_code_stops_working_once_the_budget_is_spent(self, _code):
        self.client.post(reverse("password-reset-request"), {"email": "victim@example.com"}, format="json")
        for attempt in range(10):
            self.client.post(
                reverse("password-reset-confirm"),
                {
                    "email": "victim@example.com",
                    "code": f"{attempt:06d}",
                    "new_password": "AttackerOwns!2026",
                    "confirm_password": "AttackerOwns!2026",
                },
                format="json",
            )
        cache.clear()  # prove it is the persistent budget, not the cache throttle
        response = self.client.post(
            reverse("password-reset-confirm"),
            {
                "email": "victim@example.com",
                "code": "424242",
                "new_password": "AttackerOwns!2026",
                "confirm_password": "AttackerOwns!2026",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_verification_code")

    def test_the_issue_endpoint_stops_minting_codes(self):
        for _ in range(20):
            cache.clear()
            self.client.post(reverse("password-reset-request"), {"email": "victim@example.com"}, format="json")
        issued = AccountRecoveryCode.objects.filter(user=self.user).count()
        self.assertEqual(issued, 5)


class DeactivatedAccountTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @patch("accounts.services.generate_verification_code", return_value="123456")
    def test_a_deactivated_account_cannot_verify_its_way_to_a_token(self, _code):
        from accounts.services import issue_email_verification

        user = User.objects.create_user(email="banned@example.com", password=STRONG_PASSWORD)
        issue_email_verification(user)
        user.is_active = False
        user.save()

        response = self.client.post(
            reverse("verify-email"),
            {"email": "banned@example.com", "code": "123456"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access", response.data)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_resend_does_not_issue_codes_to_a_deactivated_account(self):
        user = User.objects.create_user(email="banned@example.com", password=STRONG_PASSWORD)
        user.is_active = False
        user.save()
        response = self.client.post(
            reverse("resend-verification"), {"email": "banned@example.com"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmailVerificationCode.objects.filter(user=user).count(), 0)


class LogoutOwnershipTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.victim = User.objects.create_user(email="victim@example.com", password=STRONG_PASSWORD)
        self.attacker = User.objects.create_user(email="attacker@example.com", password=STRONG_PASSWORD)

    def test_one_account_cannot_end_another_accounts_session(self):
        victim_refresh = str(RefreshToken.for_user(self.victim))
        self.client.force_authenticate(user=self.attacker)
        response = self.client.post(reverse("logout"), {"refresh": victim_refresh}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "invalid_refresh_token")

        self.client.force_authenticate(user=None)
        still_valid = self.client.post(reverse("token-refresh"), {"refresh": victim_refresh}, format="json")
        self.assertEqual(still_valid.status_code, 200, "the victim's session was revoked anyway")

    def test_a_caller_can_still_end_its_own_session(self):
        own_refresh = str(RefreshToken.for_user(self.victim))
        self.client.force_authenticate(user=self.victim)
        response = self.client.post(reverse("logout"), {"refresh": own_refresh}, format="json")
        self.assertEqual(response.status_code, 204)


class EmailChangeSessionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(email="owner@example.com", password=STRONG_PASSWORD)
        self.user.email_verified_at = timezone.now()
        self.user.save()

    @patch("accounts.services.generate_verification_code", return_value="321321")
    def test_changing_the_email_revokes_old_sessions_and_returns_a_new_pair(self, _code):
        stale_refresh = str(RefreshToken.for_user(self.user))
        self.client.force_authenticate(user=self.user)
        self.client.post(reverse("change-email-request"), {"new_email": "new@example.com"}, format="json")
        response = self.client.post(
            reverse("change-email-confirm"),
            {"new_email": "new@example.com", "code": "321321"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

        self.client.force_authenticate(user=None)
        replayed = self.client.post(reverse("token-refresh"), {"refresh": stale_refresh}, format="json")
        self.assertEqual(replayed.status_code, 401, "a session predating the identity change survived")

        fresh = self.client.post(reverse("token-refresh"), {"refresh": response.data["refresh"]}, format="json")
        self.assertEqual(fresh.status_code, 200)


class MicrosoftIdentityClaimTests(TestCase):
    def test_an_unverified_preferred_username_is_not_accepted_as_an_email(self):
        from common.exceptions import AppError
        from accounts.social import verify_microsoft_token

        claims = {"oid": "subject-1", "preferred_username": "ceo@bigcorp.example", "tid": "common"}
        with override_settings(MICROSOFT_OAUTH_CLIENT_ID="client", MICROSOFT_OAUTH_TENANT_ID="common"):
            with patch("jwt.decode", return_value=claims), patch("jwt.PyJWKClient"):
                with self.assertRaises(AppError) as caught:
                    verify_microsoft_token("token")
        self.assertEqual(caught.exception.code, "social_identity_incomplete")

    def test_a_tenant_that_has_not_proven_domain_ownership_is_refused(self):
        from common.exceptions import AppError
        from accounts.social import verify_microsoft_token

        claims = {"oid": "s", "email": "ceo@bigcorp.example", "tid": "common", "xms_edov": False}
        with override_settings(MICROSOFT_OAUTH_CLIENT_ID="client", MICROSOFT_OAUTH_TENANT_ID="common"):
            with patch("jwt.decode", return_value=claims), patch("jwt.PyJWKClient"):
                with self.assertRaises(AppError) as caught:
                    verify_microsoft_token("token")
        self.assertEqual(caught.exception.code, "social_email_not_verified")
