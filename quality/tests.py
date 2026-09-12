from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from quality.models import (
    ApplicationStatus,
    BuyerSpec,
    CAPAStatus,
    Certificate,
    CertificateStatus,
    DocStatus,
    NCStatus,
    QualityApplication,
    QualityNonConformity,
    ResultVerdict,
    Sample,
    SampleStatus,
)


def make_user(email, **extra):
    return User.objects.create_user(
        email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), **extra,
    )


def make_org(name, org_type):
    return Organisation.objects.create(
        name=name, organisation_type=org_type,
        registration_number=name.upper().replace(" ", "-"), verification_status="verified",
    )


class QualityRoleTests(APITestCase):
    def setUp(self):
        self.operator = make_user("operator@quality.test", is_staff=True)
        self.miner_user = make_user("miner@quality.test")
        self.partner_user = make_user("partner@quality.test")
        self.regulator_user = make_user("regulator@quality.test")
        self.nobody = make_user("nobody@quality.test")

        self.miner_org = make_org("Ilesa Mining Co", OrganisationType.MINING_COMPANY)
        self.partner_org = make_org("Assay Labs", OrganisationType.LABORATORY)
        self.regulator_org = make_org("Mines Regulator", OrganisationType.REGULATOR)

        OrganisationMembership.objects.create(organisation=self.miner_org, user=self.miner_user, role=MembershipRole.OWNER)
        OrganisationMembership.objects.create(organisation=self.partner_org, user=self.partner_user, role=MembershipRole.OWNER)
        OrganisationMembership.objects.create(organisation=self.regulator_org, user=self.regulator_user, role=MembershipRole.OWNER)

    def test_me_endpoint_derives_role(self):
        cases = [
            (self.operator, "operator", True, True),
            (self.miner_user, "miner", False, False),
            (self.partner_user, "partner", True, False),
            (self.regulator_user, "regulator", True, False),
            (self.nobody, None, False, False),
        ]
        for user, role, can_review, can_decide in cases:
            self.client.force_authenticate(user)
            resp = self.client.get(reverse("quality-me"))
            self.assertEqual(resp.status_code, 200, resp.content)
            self.assertEqual(resp.data["role"], role)
            self.assertEqual(resp.data["can_review"], can_review)
            self.assertEqual(resp.data["can_decide"], can_decide)
            self.assertEqual(resp.data["is_staff"], user.is_staff)


class QualityApplicationFlowTests(APITestCase):
    def setUp(self):
        self.operator = make_user("op2@quality.test", is_staff=True)
        self.partner_user = make_user("partner2@quality.test")
        self.partner_org = make_org("Second Assay Labs", OrganisationType.LABORATORY)
        OrganisationMembership.objects.create(organisation=self.partner_org, user=self.partner_user, role=MembershipRole.OWNER)

        self.app = QualityApplication.objects.create(
            organisation=self.partner_org,
            documents=[{"id": "doc-1", "name": "Cert of incorporation", "category": "organisation",
                        "reference": "R1", "issuer": "CAC", "issued": "2024-01-01", "expires": None,
                        "status": DocStatus.PENDING, "note": "", "conditional_on": ""}],
            risk_flags=[{"id": "flag-1", "severity": "medium", "title": "PEP owner", "detail": "", "resolved": False}],
        )

    def test_document_status_update(self):
        self.client.force_authenticate(self.operator)
        url = reverse("quality-application-document-status", args=[self.app.id, "doc-1"])
        resp = self.client.patch(url, {"status": "verified"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "verified")
        self.app.refresh_from_db()
        self.assertEqual(len(self.app.audit), 1)

    def test_resolve_risk_flag(self):
        self.client.force_authenticate(self.operator)
        url = reverse("quality-application-resolve-risk-flag", args=[self.app.id, "flag-1"])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.data["resolved"])

    def test_assign_requires_review_authority(self):
        outsider = make_user("outsider2@quality.test")
        self.client.force_authenticate(outsider)
        url = reverse("quality-application-assign", args=[self.app.id])
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 403)

        self.client.force_authenticate(self.partner_user)
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "in_review")

    def test_decide_requires_operator_and_is_final(self):
        self.client.force_authenticate(self.partner_user)
        url = reverse("quality-application-decide", args=[self.app.id])
        resp = self.client.post(url, {"status": "approved", "note": "Looks good"}, format="json")
        self.assertEqual(resp.status_code, 403)

        self.client.force_authenticate(self.operator)
        resp = self.client.post(url, {"status": "approved", "note": "Looks good"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "approved")

        resp = self.client.post(url, {"status": "rejected"}, format="json")
        self.assertEqual(resp.status_code, 409)


class SampleWorkflowTests(APITestCase):
    def setUp(self):
        self.operator = make_user("op3@quality.test", is_staff=True)
        self.partner_user = make_user("partner3@quality.test")
        self.partner_org = make_org("Third Assay Labs", OrganisationType.LABORATORY)
        OrganisationMembership.objects.create(organisation=self.partner_org, user=self.partner_user, role=MembershipRole.OWNER)
        self.spec = BuyerSpec.objects.create(name="Gold Spec A", buyer_org="Buyer Co", material="gold")

    def test_full_sample_to_certificate_flow(self):
        self.client.force_authenticate(self.partner_user)

        resp = self.client.post(reverse("quality-sample-list"), {
            "material": "gold", "lot": "L1", "mine_site": "Site A", "origin": "Nigeria",
            "mass_kg": "2.5", "buyer_spec": str(self.spec.id),
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        sample_id = resp.data["id"]
        self.assertEqual(resp.data["status"], "registered")

        resp = self.client.post(reverse("quality-sample-custody", args=[sample_id]), {
            "action": "Collected", "location": "Mine gate", "seal_intact": True,
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "in_transit")
        self.assertEqual(len(resp.data["custody"]), 1)

        resp = self.client.post(reverse("quality-sample-test-request", args=[sample_id]), {
            "methods": ["ICP-MS"], "priority": "standard", "turnaround": "48h",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "testing")
        result_id = resp.data["results"][0]["id"]

        resp = self.client.patch(reverse("quality-sample-result", args=[sample_id, result_id]), {
            "verdict": "pass", "value": "99.2",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["verdict"], "pass")

        resp = self.client.post(reverse("quality-sample-review", args=[sample_id]), {
            "verdict": "pass", "note": "Meets spec",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "reviewed")

        # Only operators can issue certificates.
        resp = self.client.post(reverse("quality-sample-certificate", args=[sample_id]))
        self.assertEqual(resp.status_code, 403)

        self.client.force_authenticate(self.operator)
        resp = self.client.post(reverse("quality-sample-certificate", args=[sample_id]))
        self.assertEqual(resp.status_code, 201, resp.content)
        cert_id = resp.data["id"]
        self.assertEqual(resp.data["status"], "active")

        sample = Sample.objects.get(pk=sample_id)
        self.assertEqual(sample.status, SampleStatus.CERTIFIED)

        resp = self.client.post(reverse("quality-certificate-revoke", args=[cert_id]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "revoked")

        cert = Certificate.objects.get(pk=cert_id)
        self.assertEqual(cert.status, CertificateStatus.REVOKED)


class NonConformityFlowTests(APITestCase):
    def setUp(self):
        self.operator = make_user("op4@quality.test", is_staff=True)

    def test_capa_flow_and_close(self):
        self.client.force_authenticate(self.operator)
        resp = self.client.post(reverse("quality-non-conformity-list"), {
            "title": "Broken seal", "against": "Assay Labs", "severity": "major", "detail": "Seal was broken in transit",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        nc_id = resp.data["id"]

        # Cannot close with an open CAPA.
        resp = self.client.post(reverse("quality-non-conformity-add-corrective-action" if False else "quality-non-conformity-capa", args=[nc_id]), {
            "action": "Retrain staff", "owner": "QA Lead", "due": "2026-01-01",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        action_id = resp.data["id"]

        nc = QualityNonConformity.objects.get(pk=nc_id)
        self.assertEqual(nc.status, NCStatus.CAPA_SUBMITTED)

        resp = self.client.post(reverse("quality-non-conformity-close", args=[nc_id]))
        self.assertEqual(resp.status_code, 409)

        resp = self.client.post(reverse("quality-non-conformity-advance-capa", args=[nc_id, action_id]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], CAPAStatus.IN_PROGRESS)

        resp = self.client.post(reverse("quality-non-conformity-advance-capa", args=[nc_id, action_id]))
        self.assertEqual(resp.data["status"], CAPAStatus.COMPLETE)

        resp = self.client.post(reverse("quality-non-conformity-close", args=[nc_id]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "closed")
