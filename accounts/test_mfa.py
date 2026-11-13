from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts import totp
from accounts.models import AuthenticatorDevice, User

MINER_ORIGIN = "https://miners.beldium.com"
COMPLIANCE_ORIGIN = "https://compliance.beldium.com"
PASSWORD = "SafePassword-2026!"


class TotpTests(SimpleTestCase):
    def test_rfc6238_reference_vector(self):
        # RFC 6238 appendix B, SHA1, T=59s -> 94287082 (8 digits); 6 digits is the tail.
        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
        self.assertEqual(totp.code_at_step(secret, 59 // 30), "287082")

    def test_accepts_one_step_of_drift_and_refuses_replay(self):
        secret = totp.generate_secret()
        now = 1_800_000_000
        step = totp.current_step(now)
        self.assertEqual(totp.matching_step(secret, totp.code_at_step(secret, step - 1), now=now), step - 1)
        self.assertIsNone(totp.matching_step(secret, totp.code_at_step(secret, step - 2), now=now))
        self.assertIsNone(totp.matching_step(secret, totp.code_at_step(secret, step), after_step=step, now=now))

    def test_provisioning_uri_names_the_issuer(self):
        uri = totp.provisioning_uri("ABC", "a@b.com")
        self.assertTrue(uri.startswith("otpauth://totp/Beldium%3Aa%40b.com?"))
        self.assertIn("issuer=Beldium", uri)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MfaFlowTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            "miner@example.com", PASSWORD, email_verified_at=timezone.now(), portal="miner"
        )

    def _login(self, origin=MINER_ORIGIN):
        return self.client.post(reverse("token"), {"email": self.user.email, "password": PASSWORD}, HTTP_ORIGIN=origin)

    def _current_code(self, offset=0):
        device = AuthenticatorDevice.objects.get(user=self.user)
        return totp.code_at_step(device.secret, totp.current_step() + offset)

    def _enable(self):
        self.client.force_authenticate(self.user)
        setup = self.client.post(reverse("mfa-setup"))
        self.assertEqual(setup.status_code, status.HTTP_200_OK)
        self.assertIn("otpauth://totp/", setup.data["otpauth_url"])
        confirm = self.client.post(reverse("mfa-confirm"), {"code": self._current_code()})
        self.assertEqual(confirm.status_code, status.HTTP_200_OK, confirm.data)
        self.client.force_authenticate(None)
        return confirm.data["backup_codes"]

    def test_sign_in_without_mfa_still_returns_tokens(self):
        response = self._login()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_unconfirmed_setup_does_not_change_sign_in(self):
        self.client.force_authenticate(self.user)
        self.client.post(reverse("mfa-setup"))
        self.client.force_authenticate(None)
        self.assertIn("access", self._login().data)

    def test_enabled_mfa_swaps_password_for_a_challenge_then_tokens(self):
        backup_codes = self._enable()
        self.assertEqual(len(backup_codes), 10)

        login = self._login()
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertTrue(login.data["mfa_required"])
        self.assertNotIn("access", login.data)

        verify = self.client.post(
            reverse("mfa-verify"),
            {"mfa_token": login.data["mfa_token"], "code": self._current_code(offset=1)},
            HTTP_ORIGIN=MINER_ORIGIN,
        )
        self.assertEqual(verify.status_code, status.HTTP_200_OK, verify.data)
        self.assertIn("access", verify.data)
        self.assertIn("refresh", verify.data)

        me = self.client.get(reverse("current-user"), HTTP_AUTHORIZATION=f"Bearer {verify.data['access']}")
        self.assertTrue(me.data["mfa_enabled"])

    def test_wrong_code_is_refused_and_five_lock_the_account(self):
        self._enable()
        token = self._login().data["mfa_token"]
        for _ in range(5):
            bad = self.client.post(reverse("mfa-verify"), {"mfa_token": token, "code": "000000"}, HTTP_ORIGIN=MINER_ORIGIN)
            self.assertIn(bad.status_code, {status.HTTP_400_BAD_REQUEST, status.HTTP_429_TOO_MANY_REQUESTS})
        locked = self.client.post(
            reverse("mfa-verify"), {"mfa_token": token, "code": self._current_code(offset=1)}, HTTP_ORIGIN=MINER_ORIGIN
        )
        self.assertEqual(locked.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_backup_code_works_once(self):
        code = self._enable()[0]
        token = self._login().data["mfa_token"]
        first = self.client.post(reverse("mfa-verify"), {"mfa_token": token, "code": code.upper()}, HTTP_ORIGIN=MINER_ORIGIN)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        token = self._login().data["mfa_token"]
        again = self.client.post(reverse("mfa-verify"), {"mfa_token": token, "code": code}, HTTP_ORIGIN=MINER_ORIGIN)
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_challenge_cannot_be_redeemed_from_another_portal(self):
        self._enable()
        token = self._login().data["mfa_token"]
        response = self.client.post(
            reverse("mfa-verify"), {"mfa_token": token, "code": self._current_code(offset=1)}, HTTP_ORIGIN=COMPLIANCE_ORIGIN
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_forged_challenge_is_refused(self):
        self._enable()
        response = self.client.post(
            reverse("mfa-verify"), {"mfa_token": "nope", "code": "123456"}, HTTP_ORIGIN=MINER_ORIGIN
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_setup_is_refused_while_enabled(self):
        self._enable()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(reverse("mfa-setup")).status_code, status.HTTP_409_CONFLICT)

    def test_disable_needs_password_and_code(self):
        self._enable()
        self.client.force_authenticate(self.user)
        wrong_password = self.client.post(reverse("mfa-disable"), {"password": "nope", "code": self._current_code(offset=1)})
        self.assertEqual(wrong_password.status_code, status.HTTP_400_BAD_REQUEST)
        ok = self.client.post(reverse("mfa-disable"), {"password": PASSWORD, "code": self._current_code(offset=1)})
        self.assertEqual(ok.status_code, status.HTTP_200_OK, ok.data)
        self.assertFalse(AuthenticatorDevice.objects.filter(user=self.user).exists())
        self.client.force_authenticate(None)
        self.assertIn("access", self._login().data)
