from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from processing.models import ComplianceReport, ProcessingApplication, Processor, ProcessingType, TraceabilityRun
from processing.permissions import audience, can_decide


def make_user(email):
    return User.objects.create_user(email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now())


class AccessControlRegressionTests(APITestCase):
    def setUp(self):
        self.attacker = make_user("attacker@evil.test")
        self.victim_org = Organisation.objects.create(
            name="Victim Processing Ltd", organisation_type=OrganisationType.MINING_COMPANY,
            verification_status="verified")
        self.victim_processor = Processor.objects.create(
            name="Victim Plant", organisation=self.victim_org, processing_type=ProcessingType.SMELTING)

    def test_A_self_declared_operator_org_grants_nothing_until_verified(self):
        self.client.force_authenticate(self.attacker)
        r = self.client.post(reverse("organisation-list"), {
            "name": "Totally Legit Compliance Partners", "registration_number": "RC-999999",
            "organisation_type": OrganisationType.COMPLIANCE_PARTNER, "country": "Nigeria"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.attacker.refresh_from_db()
        self.assertIsNone(audience(self.attacker))
        self.assertFalse(can_decide(self.attacker))
        rp = self.client.get(reverse("processor-list"))
        self.assertEqual(rp.status_code, 403, rp.data)

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

    def test_B_reports_scoped_to_whole_register_only(self):
        ComplianceReport.objects.create(kind="regional", title="National register extract", scope="All regions")
        OrganisationMembership.objects.create(
            organisation=self.victim_org, user=self.attacker, role=MembershipRole.READ_ONLY)
        self.client.force_authenticate(self.attacker)
        r = self.client.get(reverse("processing-report-list"))
        self.assertEqual(r.data["count"], 0)

    def test_C_processor_cannot_amend_qc_verdict(self):
        OrganisationMembership.objects.create(
            organisation=self.victim_org, user=self.attacker, role=MembershipRole.READ_ONLY)
        run = TraceabilityRun.objects.create(
            processor=self.victim_processor, input_batch="IN-1", input_mass_kg=1000,
            output_batch="OUT-1", output_mass_kg=900, started_at=timezone.now(), qc_verdict="fail")
        self.client.force_authenticate(self.attacker)
        r = self.client.patch(reverse("processing-run-detail", args=[run.id]),
                              {"qc_verdict": "pass"}, format="json")
        run.refresh_from_db()
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(run.qc_verdict, "fail")

    def test_C2_run_processor_immutable(self):
        OrganisationMembership.objects.create(
            organisation=self.victim_org, user=self.attacker, role=MembershipRole.OWNER)
        run = TraceabilityRun.objects.create(
            processor=self.victim_processor, input_batch="IN-1", input_mass_kg=1000,
            output_batch="OUT-1", output_mass_kg=900, started_at=timezone.now())
        other_processor = Processor.objects.create(
            name="Attacker Plant", organisation=self.victim_org, processing_type=ProcessingType.SMELTING)
        self.client.force_authenticate(self.attacker)
        r = self.client.patch(reverse("processing-run-detail", args=[run.id]),
                              {"processor": str(other_processor.id)}, format="json")
        self.assertEqual(r.status_code, 400, r.data)

    def test_D_application_cannot_be_reattributed(self):
        mine_org = Organisation.objects.create(
            name="Attacker Mining", organisation_type=OrganisationType.MINING_COMPANY, verification_status="verified")
        OrganisationMembership.objects.create(organisation=mine_org, user=self.attacker, role=MembershipRole.OWNER)
        app = ProcessingApplication.objects.create(
            company="Attacker Mining", organisation=mine_org, created_by=self.attacker,
            processing_type=ProcessingType.SMELTING)
        self.client.force_authenticate(self.attacker)
        r = self.client.patch(reverse("processing-application-detail", args=[app.id]),
                              {"organisation": str(self.victim_org.id), "processor": str(self.victim_processor.id)},
                              format="json")
        app.refresh_from_db()
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(app.organisation_id, mine_org.id)
        self.assertIsNone(app.processor_id)

    def test_E_incident_status_not_settable_at_creation(self):
        from organisations.models import OrganisationMembership as OM
        OM.objects.create(organisation=self.victim_org, user=self.attacker, role=MembershipRole.OWNER)
        self.client.force_authenticate(self.attacker)
        r = self.client.post(reverse("processing-incident-list"), {
            "processor": str(self.victim_processor.id), "incident_type": "spill",
            "severity": "low", "status": "closed", "summary": "already handled",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "reported")
