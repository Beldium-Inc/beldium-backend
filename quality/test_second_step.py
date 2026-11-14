"""Two-step sign-in required for internal and partner Q&C roles (task G8)."""
from django.test import override_settings
from django.urls import reverse

from accounts import mfa, totp
from quality.test_access import QualityAccessBase


def pair_app(user):
    secret = mfa.start_setup(user)["secret"]
    mfa.confirm_setup(user, totp.code_at_step(secret, totp.current_step()))


@override_settings(SECOND_FACTOR_ENFORCED=True)
class SecondStepRequiredTests(QualityAccessBase):
    def test_staff_without_the_app_is_refused_with_a_clear_code(self):
        self.as_user(self.operator)
        response = self.client.get(reverse("quality-sample-list"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "second_factor_setup_required")
        self.assertEqual(self.client.get(reverse("quality-dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("vault-document-list"), {"sector": "quality"}).status_code, 403)

    def test_me_stays_open_and_says_what_is_needed(self):
        self.as_user(self.operator)
        response = self.client.get(reverse("quality-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["second_factor_required"])
        self.assertFalse(response.data["second_factor_enrolled"])

    def test_paired_staff_gets_in(self):
        pair_app(self.operator)
        self.as_user(self.operator)
        self.assertEqual(self.client.get(reverse("quality-sample-list")).status_code, 200)

    def test_supplier_role_is_not_asked(self):
        # A miner maps to the Supplier role, which signs in without the second step.
        self.as_user(self.miner_a)
        self.assertEqual(self.client.get(reverse("quality-sample-list")).status_code, 200)

    def test_laboratory_is_asked(self):
        self.as_user(self.lab1)
        self.assertEqual(self.client.get(reverse("quality-sample-list")).status_code, 403)

    def test_cannot_turn_it_off_while_required(self):
        pair_app(self.operator)
        self.as_user(self.operator)
        device = mfa.confirmed_device(self.operator)
        code = totp.code_at_step(device.secret, totp.current_step() + 1)
        response = self.client.post(reverse("mfa-disable"), {"password": "Str0ng-Passw0rd!", "code": code}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(mfa.is_enabled(self.operator))


class SwitchedOffTests(QualityAccessBase):
    def test_nothing_changes_while_off(self):
        self.as_user(self.operator)
        self.assertEqual(self.client.get(reverse("quality-sample-list")).status_code, 200)
        self.assertFalse(self.client.get(reverse("quality-me")).data["second_factor_required"])
