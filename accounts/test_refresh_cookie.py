"""The refresh token in an HttpOnly cookie (task G9)."""
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from accounts.tests import COMPLIANCE_ORIGIN
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from quality.models import QualityRoleAssignment, RoleAssignmentStatus

PASSWORD = "Str0ng-Passw0rd!"
COOKIE = "beldium_refresh"


def make_user(email, **extra):
    return User.objects.create_user(email=email, password=PASSWORD, email_verified_at=timezone.now(), **extra)


class RefreshCookieTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("person@beldium.test")

    def sign_in(self, user=None):
        return self.client.post(
            reverse("token"), {"email": (user or self.user).email, "password": PASSWORD}, HTTP_ORIGIN=COMPLIANCE_ORIGIN,
        )

    def test_sign_in_sets_an_http_only_cookie(self):
        response = self.sign_in()
        cookie = response.cookies[COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], "/api/v1/auth/")
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie.value, response.data["refresh"])

    @override_settings(REFRESH_TOKEN_IN_BODY=False)
    def test_body_copy_can_be_switched_off(self):
        response = self.sign_in()
        self.assertNotIn("refresh", response.data)
        self.assertIn(COOKIE, response.cookies)

    def test_cookie_refresh_rotates_the_token(self):
        login = self.sign_in()
        old = login.cookies[COOKIE].value
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.post(reverse("token-refresh"), {}, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("access", response.data)
        new = response.cookies[COOKIE].value
        self.assertNotEqual(new, old)
        # The old token is revoked.
        replay = self.client.post(reverse("token-refresh"), {"refresh": old}, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(replay.status_code, 401)

    def test_cookie_needs_a_portal_origin(self):
        login = self.sign_in()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.post(reverse("token-refresh"), {}, HTTP_ORIGIN="https://evil.example")
        self.assertEqual(response.status_code, 401)

    def test_idle_office_session_still_ends(self):
        self.sign_in()
        response = self.client.post(reverse("token-refresh"), {}, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, 401)

    def test_field_role_refreshes_after_access_lapsed(self):
        org = Organisation.objects.create(
            name="Field Co", organisation_type=OrganisationType.INSPECTION_BODY,
            registration_number="FIELD", verification_status="verified",
        )
        sampler = make_user("sampler@field.test")
        OrganisationMembership.objects.create(organisation=org, user=sampler, role=MembershipRole.OWNER)
        QualityRoleAssignment.objects.create(
            user=sampler, role_id="SMP", organisation=org, status=RoleAssignmentStatus.ACTIVE,
            valid_from=timezone.localdate(),
        )
        self.sign_in(sampler)
        response = self.client.post(reverse("token-refresh"), {}, HTTP_ORIGIN=COMPLIANCE_ORIGIN)
        self.assertEqual(response.status_code, 200, response.data)

    def test_logout_uses_the_cookie_and_clears_it(self):
        login = self.sign_in()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.post(reverse("logout"), {}, format="json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies[COOKIE].value, "")
        replay = self.client.post(
            reverse("token-refresh"), {"refresh": login.data["refresh"]}, HTTP_ORIGIN=COMPLIANCE_ORIGIN,
        )
        self.assertEqual(replay.status_code, 401)

    def test_wrong_owner_is_refused(self):
        other = make_user("other@beldium.test")
        self.client.force_authenticate(self.user)
        response = self.client.post(reverse("token-refresh"), {"refresh": str(RefreshToken.for_user(other))})
        self.assertEqual(response.status_code, 401)
