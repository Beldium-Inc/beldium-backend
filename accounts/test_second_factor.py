"""Second factor at sign in with an authenticator app (task G8)."""
import time

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import second_factor as sf
from accounts.models import SecondFactorChallenge, SecondFactorDevice, User
from accounts.tests import COMPLIANCE_ORIGIN
from quality.models import QualityRoleAssignment, RoleAssignmentStatus

PASSWORD = "Str0ng-Passw0rd!"


def make_user(email, **extra):
    return User.objects.create_user(email=email, password=PASSWORD, email_verified_at=timezone.now(), **extra)


class TotpTests(APITestCase):
    def test_rfc6238_reference_value(self):
        # RFC 6238 appendix B, SHA1, time 59, secret "12345678901234567890".
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(sf._code_for_step(secret, 59 // 30), "287082")

    def test_code_is_accepted_once_and_window_is_one_step(self):
        secret = sf.new_secret()
        now = time.time()
        code = sf.code_at(secret, now)
        step = sf.matching_step(secret, code, now=now)
        self.assertIsNotNone(step)
        self.assertIsNone(sf.matching_step(secret, code, after_step=step, now=now))
        self.assertIsNone(sf.matching_step(secret, sf.code_at(secret, now - 120), now=now))
        self.assertIsNone(sf.matching_step(secret, "abc123", now=now))

    def test_secret_is_stored_encrypted(self):
        token = sf.encrypt_secret("ABCDEF")
        self.assertNotIn("ABCDEF", token)
        self.assertEqual(sf.decrypt_secret(token), "ABCDEF")


@override_settings(SECOND_FACTOR_ENFORCED=True)
class SignInTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_user("desk@beldium.test", is_staff=True)
        self.supplier = make_user("supplier@mine.test")
        QualityRoleAssignment.objects.create(
            user=self.supplier, role_id="SUP", status=RoleAssignmentStatus.ACTIVE, valid_from=timezone.localdate(),
        )

    def sign_in(self, user):
        return self.client.post(reverse("token"), {"email": user.email, "password": PASSWORD}, HTTP_ORIGIN=COMPLIANCE_ORIGIN)

    def enrol(self, user):
        secret = sf.new_secret()
        SecondFactorDevice.objects.create(
            user=user, secret_encrypted=sf.encrypt_secret(secret), confirmed_at=timezone.now(),
        )
        return secret

    def test_internal_user_gets_a_challenge_not_tokens(self):
        response = self.sign_in(self.staff)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["second_factor_required"])
        self.assertTrue(response.data["enrolment_required"])
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)

    def test_supplier_signs_in_as_before(self):
        response = self.sign_in(self.supplier)
        self.assertIn("access", response.data)

    @override_settings(SECOND_FACTOR_ENFORCED=False)
    def test_switched_off_issues_tokens(self):
        self.assertIn("access", self.sign_in(self.staff).data)

    def test_code_swaps_challenge_for_tokens_once(self):
        secret = self.enrol(self.staff)
        challenge = self.sign_in(self.staff).data["challenge_token"]
        url = reverse("token-second-factor")
        response = self.client.post(url, {"challenge_token": challenge, "code": sf.code_at(secret)})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("access", response.data)
        again = self.client.post(url, {"challenge_token": challenge, "code": sf.code_at(secret)})
        self.assertEqual(again.data["error"]["code"], "challenge_expired")

    def test_wrong_codes_lock_the_challenge(self):
        self.enrol(self.staff)
        challenge = self.sign_in(self.staff).data["challenge_token"]
        url = reverse("token-second-factor")
        for _ in range(sf.MAX_FAILED_ATTEMPTS):
            response = self.client.post(url, {"challenge_token": challenge, "code": "000000"})
            self.assertIn(response.data["error"]["code"], {"second_factor_invalid", "throttled"})
        challenge_row = SecondFactorChallenge.objects.get()
        self.assertEqual(challenge_row.failed_attempts, sf.MAX_FAILED_ATTEMPTS)
        cache.clear()
        response = self.client.post(url, {"challenge_token": challenge, "code": "000000"})
        self.assertEqual(response.data["error"]["code"], "second_factor_locked")

    def test_expired_challenge(self):
        self.enrol(self.staff)
        challenge = self.sign_in(self.staff).data["challenge_token"]
        SecondFactorChallenge.objects.update(expires_at=timezone.now())
        response = self.client.post(reverse("token-second-factor"), {"challenge_token": challenge, "code": "123456"})
        self.assertEqual(response.status_code, 401)

    def test_not_enrolled_cannot_pass(self):
        challenge = self.sign_in(self.staff).data["challenge_token"]
        response = self.client.post(reverse("token-second-factor"), {"challenge_token": challenge, "code": "123456"})
        self.assertEqual(response.data["error"]["code"], "second_factor_not_enrolled")

    def test_enrol_through_the_challenge_then_recovery_code_works_once(self):
        challenge = self.sign_in(self.staff).data["challenge_token"]
        started = self.client.post(reverse("second-factor-enrol"), {"challenge_token": challenge, "method": "totp"})
        self.assertEqual(started.status_code, 200, started.data)
        self.assertTrue(started.data["otpauth_uri"].startswith("otpauth://totp/Beldium"))
        wrong = self.client.post(reverse("second-factor-enrol-confirm"), {"challenge_token": challenge, "code": "000000"})
        self.assertEqual(wrong.data["error"]["code"], "second_factor_invalid")
        confirmed = self.client.post(
            reverse("second-factor-enrol-confirm"),
            {"challenge_token": challenge, "code": sf.code_at(started.data["secret"])},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertIn("access", confirmed.data)
        self.assertEqual(len(confirmed.data["recovery_codes"]), sf.RECOVERY_CODE_COUNT)
        self.assertTrue(sf.is_enrolled(self.staff))
        # A second enrolment is refused.
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(reverse("second-factor-enrol"), {}).status_code, 409)
        self.client.force_authenticate(None)
        # A recovery code stands in for a lost phone, once.
        recovery = confirmed.data["recovery_codes"][0]
        url = reverse("token-second-factor")
        challenge = self.sign_in(self.staff).data["challenge_token"]
        self.assertIn("access", self.client.post(url, {"challenge_token": challenge, "code": recovery}).data)
        challenge = self.sign_in(self.staff).data["challenge_token"]
        reuse = self.client.post(url, {"challenge_token": challenge, "code": recovery})
        self.assertEqual(reuse.data["error"]["code"], "second_factor_invalid")

    def test_enrol_needs_a_challenge_or_a_session(self):
        response = self.client.post(reverse("second-factor-enrol"), {})
        self.assertEqual(response.status_code, 401)

    def test_quality_me_reports_enrolment(self):
        self.enrol(self.staff)
        self.client.force_authenticate(self.staff)
        self.assertTrue(self.client.get(reverse("quality-me")).data["second_factor_enrolled"])
