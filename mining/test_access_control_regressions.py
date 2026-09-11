from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from mining.models import MineSite, NonConformity
from mining.permissions import audience, can_decide
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType


def make_user(email):
    return User.objects.create_user(email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now())


class AccessControlRegressionTests(APITestCase):
    def setUp(self):
        self.attacker = make_user("attacker@evil.test")
        self.victim_org = Organisation.objects.create(
            name="Victim Mining Ltd", organisation_type=OrganisationType.MINING_COMPANY,
            verification_status="verified")
        self.victim_site = MineSite.objects.create(
            name="Victim Site", organisation=self.victim_org, mineral="Gold", state="Kaduna")

    def test_A_self_declared_operator_org_grants_nothing_until_verified(self):
        self.client.force_authenticate(self.attacker)
        r = self.client.post(reverse("organisation-list"), {
            "name": "Totally Legit Compliance Partners", "registration_number": "RC-888888",
            "organisation_type": OrganisationType.COMPLIANCE_PARTNER, "country": "Nigeria"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.attacker.refresh_from_db()
        self.assertIsNone(audience(self.attacker))
        self.assertFalse(can_decide(self.attacker))
        rs = self.client.get(reverse("mining-site-list"))
        self.assertEqual(rs.status_code, 403, rs.data)

    def test_A2_org_type_locked_once_set(self):
        self.client.force_authenticate(self.attacker)
        org = Organisation.objects.create(
            name="Attacker Mining", organisation_type=OrganisationType.MINING_COMPANY, verification_status="verified")
        OrganisationMembership.objects.create(organisation=org, user=self.attacker, role=MembershipRole.OWNER)
        r = self.client.patch(reverse("organisation-detail", args=[org.id]),
                              {"organisation_type": OrganisationType.COMPLIANCE_PARTNER}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        org.refresh_from_db()
        self.assertEqual(org.organisation_type, OrganisationType.MINING_COMPANY)

    def test_B_audit_trail_scoped_to_whole_register_only(self):
        OrganisationMembership.objects.create(
            organisation=self.victim_org, user=self.attacker, role=MembershipRole.READ_ONLY)
        self.client.force_authenticate(self.attacker)
        r = self.client.get(reverse("mining-audit-list"))
        self.assertEqual(r.data["count"], 0)

    def test_C_non_operator_cannot_amend_a_finding(self):
        OrganisationMembership.objects.create(
            organisation=self.victim_org, user=self.attacker, role=MembershipRole.OWNER)
        finding = NonConformity.objects.create(
            site=self.victim_site, title="Unsafe tailings dam", severity="critical",
            deadline=timezone.localdate() + timedelta(days=7))
        self.client.force_authenticate(self.attacker)
        r = self.client.patch(reverse("mining-non-conformity-detail", args=[finding.id]),
                              {"severity": "minor"}, format="json")
        finding.refresh_from_db()
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(finding.severity, "critical")

    def test_D_site_cannot_be_reattributed(self):
        mine_org = Organisation.objects.create(
            name="Attacker Mining", organisation_type=OrganisationType.MINING_COMPANY, verification_status="verified")
        OrganisationMembership.objects.create(organisation=mine_org, user=self.attacker, role=MembershipRole.OWNER)
        site = MineSite.objects.create(name="Attacker Site", organisation=mine_org, mineral="Tin", state="Plateau")
        self.client.force_authenticate(self.attacker)
        r = self.client.patch(reverse("mining-site-detail", args=[site.id]),
                              {"organisation": str(self.victim_org.id)}, format="json")
        site.refresh_from_db()
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(site.organisation_id, mine_org.id)

    def test_E_outsider_cannot_reach_another_orgs_site_by_guessing_its_id(self):
        OrganisationMembership.objects.create(
            organisation=Organisation.objects.create(
                name="Bystander Mining", organisation_type=OrganisationType.MINING_COMPANY, verification_status="verified"),
            user=self.attacker, role=MembershipRole.OWNER)
        self.client.force_authenticate(self.attacker)
        r = self.client.get(reverse("mining-site-detail", args=[self.victim_site.id]))
        self.assertEqual(r.status_code, 404, r.data)

    def test_F_outsider_cannot_upload_documents_to_another_orgs_site(self):
        OrganisationMembership.objects.create(
            organisation=Organisation.objects.create(
                name="Bystander Mining 2", organisation_type=OrganisationType.MINING_COMPANY, verification_status="verified"),
            user=self.attacker, role=MembershipRole.OWNER)
        self.client.force_authenticate(self.attacker)
        r = self.client.post(reverse("mining-document-list"), {
            "site": str(self.victim_site.id), "name": "Forged Certificate",
        }, format="json")
        self.assertEqual(r.status_code, 403, r.data)
