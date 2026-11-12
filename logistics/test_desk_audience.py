from django.urls import reverse
from rest_framework.test import APITestCase

from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from logistics.tests import make_org, make_user


class LogisticsDeskAudienceTests(APITestCase):
    """The prototype asks this endpoint whether a caller holds the logistics desk audience.

    The answer comes from organisations.access.audience, so a compliance partner only
    counts once its organisation is verified, and staff are always operators.
    """

    def setUp(self):
        self.url = reverse("logistics-desk")

    def make_member(self, email, *, org_type, verification_status="verified", role=MembershipRole.REVIEWER):
        user = make_user(email)
        org = Organisation.objects.create(
            name=f"Org of {email}",
            organisation_type=org_type,
            verification_status=verification_status,
        )
        OrganisationMembership.objects.create(organisation=org, user=user, role=role)
        return user

    def test_anonymous_caller_is_refused(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (401, 403))

    def test_verified_compliance_partner_gets_operator_audience(self):
        user = self.make_member("desk@partner.test", org_type=OrganisationType.COMPLIANCE_PARTNER)
        self.client.force_authenticate(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"audience": "operator"})

    def test_unverified_compliance_partner_gets_no_audience(self):
        user = self.make_member(
            "pending@partner.test",
            org_type=OrganisationType.COMPLIANCE_PARTNER,
            verification_status="under_review",
        )
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.url).data, {"audience": None})

    def test_ordinary_company_member_is_not_the_desk(self):
        user = make_user("owner@haulage.test")
        org = make_org("Ilesa Heavy Haulage")
        OrganisationMembership.objects.create(organisation=org, user=user, role=MembershipRole.OWNER)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.url).data, {"audience": "member"})

    def test_user_with_no_membership_gets_no_audience(self):
        self.client.force_authenticate(make_user("nobody@example.test"))
        self.assertEqual(self.client.get(self.url).data, {"audience": None})

    def test_staff_gets_operator_audience(self):
        self.client.force_authenticate(make_user("staff@beldium.test", is_staff=True))
        self.assertEqual(self.client.get(self.url).data, {"audience": "operator"})

    def test_verified_regulator_gets_regulator_audience(self):
        user = self.make_member("oversight@frsc.test", org_type=OrganisationType.REGULATOR)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(self.url).data, {"audience": "regulator"})

    def test_endpoint_is_read_only(self):
        user = self.make_member("desk2@partner.test", org_type=OrganisationType.COMPLIANCE_PARTNER)
        self.client.force_authenticate(user)
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 405)
