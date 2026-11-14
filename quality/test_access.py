"""Access and gate rules on the quality endpoints.

Each test here matches a fault proved by QC_FAULT_PROBE_REPORT.md (10 October
2026), or checks that the wrong role or the wrong owner is refused.
"""
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from quality.models import (
    BuyerSpec,
    Certificate,
    QualityApplication,
    QualityNonConformity,
    Sample,
    SampleStatus,
)


def make_user(email, **extra):
    return User.objects.create_user(
        email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), **extra,
    )


def make_org(name, org_type, status="verified"):
    return Organisation.objects.create(
        name=name, organisation_type=org_type,
        registration_number=name.upper().replace(" ", "-"), verification_status=status,
    )


def member(org, email, role=MembershipRole.OWNER):
    user = make_user(email)
    OrganisationMembership.objects.create(organisation=org, user=user, role=role)
    return user


class QualityAccessBase(APITestCase):
    def setUp(self):
        self.operator = make_user("desk@beldium.test", is_staff=True)
        self.nobody = make_user("nobody@no-org.test")

        self.mine_a = make_org("Mine A", OrganisationType.MINING_COMPANY)
        self.mine_b = make_org("Mine B", OrganisationType.MINING_COMPANY)
        self.lab_one = make_org("Lab One", OrganisationType.LABORATORY)
        self.lab_two = make_org("Lab Two", OrganisationType.LABORATORY)
        self.regulator_org = make_org("Mines Regulator", OrganisationType.REGULATOR)

        self.miner_a = member(self.mine_a, "a.owner@mine-a.test")
        self.miner_b = member(self.mine_b, "b.owner@mine-b.test")
        self.lab1 = member(self.lab_one, "lab1@lab-one.test")
        self.lab2 = member(self.lab_two, "lab2@lab-two.test")
        self.regulator = member(self.regulator_org, "reg@regulator.test")

        self.spec = BuyerSpec.objects.create(
            name="Gold Spec", buyer_org="Buyer Co", material="gold",
            limits=[{"analyte": "Au", "unit": "%", "min": "95", "max": "", "method": "ICP-MS"}],
        )
        self.sample = Sample.objects.create(
            material="gold", buyer_spec=self.spec, miner_organisation=self.mine_a,
            miner_org=self.mine_a.name, registered_by=self.miner_a, status=SampleStatus.IN_TRANSIT,
        )

    def as_user(self, user):
        self.client.force_authenticate(user)

    def url(self, name, *args):
        return reverse(name, args=args)

    def request_test(self, laboratory=None):
        self.as_user(self.operator)
        body = {"methods": ["ICP-MS"], "priority": "standard"}
        if laboratory is not None:
            body["laboratory"] = str(laboratory.id)
        resp = self.client.post(self.url("quality-sample-test-request", self.sample.id), body, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.sample.refresh_from_db()
        return resp

    def enter_value(self, value):
        self.as_user(self.operator)
        result_id = self.sample.results[0]["id"]
        resp = self.client.patch(
            self.url("quality-sample-result", self.sample.id, result_id),
            {"value": value, "uncertainty": "0.1"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.sample.refresh_from_db()


class SampleOwnershipTests(QualityAccessBase):
    def test_other_mine_cannot_add_custody_to_a_sample(self):
        self.as_user(self.miner_b)
        resp = self.client.post(self.url("quality-sample-custody", self.sample.id), {"action": "Tampered"}, format="json")
        self.assertEqual(resp.status_code, 404)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.custody, [])

    def test_owner_mine_can_add_custody(self):
        self.as_user(self.miner_a)
        resp = self.client.post(self.url("quality-sample-custody", self.sample.id), {"action": "Collected"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["status"], "in_transit")

    def test_unassigned_laboratory_cannot_take_over_a_sample(self):
        self.as_user(self.lab2)
        resp = self.client.post(
            self.url("quality-sample-test-request", self.sample.id),
            {"methods": ["XRF"], "priority": "standard"}, format="json",
        )
        self.assertEqual(resp.status_code, 404)
        self.sample.refresh_from_db()
        self.assertIsNone(self.sample.partner_organisation)
        self.assertEqual(self.client.get(self.url("quality-sample-detail", self.sample.id)).status_code, 404)

    def test_laboratory_cannot_assign_a_laboratory(self):
        self.sample.partner_organisation = self.lab_one
        self.sample.save()
        self.as_user(self.lab1)
        resp = self.client.post(
            self.url("quality-sample-test-request", self.sample.id),
            {"methods": ["XRF"], "priority": "standard", "laboratory": str(self.lab_two.id)}, format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.partner_organisation, self.lab_one)

    def test_operator_assigns_laboratory_and_only_that_laboratory_enters_results(self):
        self.request_test(laboratory=self.lab_one)
        self.assertEqual(self.sample.partner_organisation, self.lab_one)
        result_id = self.sample.results[0]["id"]
        url = self.url("quality-sample-result", self.sample.id, result_id)

        self.as_user(self.lab2)
        self.assertEqual(self.client.patch(url, {"value": "99"}, format="json").status_code, 404)

        self.as_user(self.lab1)
        resp = self.client.patch(url, {"value": "99"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data["verdict"], "pass")

    def test_unverified_or_unapproved_laboratory_cannot_be_assigned(self):
        draft_lab = make_org("Draft Lab", OrganisationType.LABORATORY, status="under_review")
        self.as_user(self.operator)
        resp = self.client.post(
            self.url("quality-sample-test-request", self.sample.id),
            {"methods": ["ICP-MS"], "priority": "standard", "laboratory": str(draft_lab.id)}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_test_request_cannot_be_repeated(self):
        self.request_test(laboratory=self.lab_one)
        self.as_user(self.operator)
        resp = self.client.post(
            self.url("quality-sample-test-request", self.sample.id),
            {"methods": ["XRF"], "priority": "standard", "laboratory": str(self.lab_two.id)}, format="json",
        )
        self.assertEqual(resp.status_code, 409)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.partner_organisation, self.lab_one)

    def test_only_operator_sets_status_by_hand(self):
        self.request_test(laboratory=self.lab_one)
        url = self.url("quality-sample-detail", self.sample.id)
        self.as_user(self.lab1)
        self.assertEqual(self.client.patch(url, {"status": "rejected"}, format="json").status_code, 403)
        self.as_user(self.miner_a)
        self.assertEqual(self.client.patch(url, {"status": "rejected"}, format="json").status_code, 403)
        self.as_user(self.regulator)
        self.assertEqual(self.client.patch(url, {"status": "rejected"}, format="json").status_code, 404)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.status, SampleStatus.TESTING)

    def test_regulator_reads_but_cannot_add_custody(self):
        self.as_user(self.regulator)
        self.assertEqual(self.client.get(self.url("quality-sample-detail", self.sample.id)).status_code, 200)
        resp = self.client.post(self.url("quality-sample-custody", self.sample.id), {"action": "Moved"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_suspended_organisation_loses_access_at_once(self):
        self.request_test(laboratory=self.lab_one)
        self.as_user(self.lab1)
        self.assertEqual(self.client.get(self.url("quality-sample-detail", self.sample.id)).status_code, 200)

        self.lab_one.verification_status = "suspended"
        self.lab_one.save()
        self.assertEqual(self.client.get(self.url("quality-sample-detail", self.sample.id)).status_code, 404)
        self.assertIsNone(self.client.get(reverse("quality-me")).data["role"])

        self.mine_a.verification_status = "suspended"
        self.mine_a.save()
        self.as_user(self.miner_a)
        resp = self.client.post(reverse("quality-sample-list"), {"material": "gold"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_unverified_laboratory_has_no_partner_role(self):
        draft_lab = make_org("Pending Lab", OrganisationType.LABORATORY, status="under_review")
        user = member(draft_lab, "pending@lab.test")
        self.as_user(user)
        self.assertIsNone(self.client.get(reverse("quality-me")).data["role"])

    def test_custody_refused_once_sample_is_final(self):
        Sample.objects.filter(pk=self.sample.pk).update(status=SampleStatus.REJECTED)
        self.as_user(self.miner_a)
        resp = self.client.post(self.url("quality-sample-custody", self.sample.id), {"action": "Moved"}, format="json")
        self.assertEqual(resp.status_code, 409)


class ReviewAndCertificateGateTests(QualityAccessBase):
    def review(self, user, verdict):
        self.as_user(user)
        return self.client.post(self.url("quality-sample-review", self.sample.id), {"verdict": verdict}, format="json")

    def certify(self):
        self.as_user(self.operator)
        return self.client.post(self.url("quality-sample-certificate", self.sample.id))

    def test_laboratory_and_regulator_cannot_review(self):
        self.request_test(laboratory=self.lab_one)
        self.enter_value("99")
        self.assertEqual(self.review(self.lab1, "pass").status_code, 403)
        self.assertEqual(self.review(self.regulator, "pass").status_code, 403)
        self.assertEqual(self.review(self.miner_a, "pass").status_code, 403)

    def test_review_cannot_be_repeated(self):
        self.request_test(laboratory=self.lab_one)
        self.enter_value("99")
        self.assertEqual(self.review(self.operator, "conditional").status_code, 200)
        self.assertEqual(self.review(self.operator, "pass").status_code, 409)

    def test_pass_review_refused_when_a_result_fails(self):
        self.request_test()
        self.enter_value("40")
        self.assertEqual(self.review(self.operator, "pass").status_code, 409)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.status, SampleStatus.TESTING)

    def test_pass_review_refused_when_a_result_is_missing(self):
        self.request_test()
        self.assertEqual(self.review(self.operator, "pass").status_code, 409)

    def test_pending_review_verdict_refused(self):
        self.request_test()
        self.enter_value("99")
        self.assertEqual(self.review(self.operator, "pending").status_code, 400)

    def test_results_locked_after_review(self):
        self.request_test(laboratory=self.lab_one)
        self.enter_value("99")
        self.assertEqual(self.review(self.operator, "pass").status_code, 200)
        self.as_user(self.lab1)
        resp = self.client.patch(
            self.url("quality-sample-result", self.sample.id, self.sample.results[0]["id"]), {"value": "10"}, format="json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_certificate_refused_when_review_is_not_pass(self):
        self.request_test()
        self.enter_value("99")
        self.assertEqual(self.review(self.operator, "conditional").status_code, 200)
        self.assertEqual(self.certify().status_code, 409)
        self.assertFalse(Certificate.objects.exists())

    def test_certificate_refused_when_stored_results_fail_despite_a_pass_review(self):
        # A record left by the old code: reviewed as pass with a failed result.
        self.request_test()
        rows = self.sample.results
        rows[0]["value"] = "40"
        rows[0]["verdict"] = "pass"
        Sample.objects.filter(pk=self.sample.pk).update(
            results=rows, status=SampleStatus.REVIEWED, quality_review={"verdict": "pass"},
        )
        self.assertEqual(self.certify().status_code, 409)
        self.assertFalse(Certificate.objects.exists())

    def test_certificate_refused_when_results_are_missing(self):
        Sample.objects.filter(pk=self.sample.pk).update(
            results=[], status=SampleStatus.REVIEWED, quality_review={"verdict": "pass"},
        )
        self.assertEqual(self.certify().status_code, 409)

    def test_certificate_refused_for_wrong_role(self):
        self.request_test(laboratory=self.lab_one)
        self.enter_value("99")
        self.assertEqual(self.review(self.operator, "pass").status_code, 200)
        for user in (self.lab1, self.miner_a, self.regulator):
            self.as_user(user)
            self.assertEqual(self.client.post(self.url("quality-sample-certificate", self.sample.id)).status_code, 403)
        self.assertEqual(self.certify().status_code, 201)


class DashboardAccessTests(QualityAccessBase):
    def test_dashboard_closed_to_everyone_but_desk_and_oversight(self):
        url = reverse("quality-dashboard")
        for user in (self.nobody, self.miner_a, self.lab1):
            self.as_user(user)
            self.assertEqual(self.client.get(url).status_code, 403, user.email)
        for user in (self.operator, self.regulator):
            self.as_user(user)
            self.assertEqual(self.client.get(url).status_code, 200, user.email)


class NonConformityAccessTests(QualityAccessBase):
    def setUp(self):
        super().setUp()
        self.nc = QualityNonConformity.objects.create(title="Broken seal", against="Lab One", raised_by_user=self.operator)

    def test_outsiders_cannot_read(self):
        for user in (self.miner_b, self.lab2, self.nobody):
            self.as_user(user)
            self.assertEqual(self.client.get(self.url("quality-non-conformity-detail", self.nc.id)).status_code, 404)
            self.assertEqual(self.client.get(reverse("quality-non-conformity-list")).data["count"], 0)

    def test_regulator_reads_but_cannot_close(self):
        self.as_user(self.regulator)
        self.assertEqual(self.client.get(self.url("quality-non-conformity-detail", self.nc.id)).status_code, 200)
        self.assertEqual(self.client.post(self.url("quality-non-conformity-close", self.nc.id)).status_code, 403)

    def test_only_operator_raises(self):
        body = {"title": "Late sample", "severity": "minor"}
        for user in (self.lab1, self.regulator, self.miner_a):
            self.as_user(user)
            self.assertEqual(self.client.post(reverse("quality-non-conformity-list"), body, format="json").status_code, 403)
        self.as_user(self.operator)
        self.assertEqual(self.client.post(reverse("quality-non-conformity-list"), body, format="json").status_code, 201)

    def test_unrelated_laboratory_cannot_add_corrective_action(self):
        self.as_user(self.lab2)
        resp = self.client.post(self.url("quality-non-conformity-capa", self.nc.id), {"action": "Retrain"}, format="json")
        self.assertEqual(resp.status_code, 403)
        self.nc.refresh_from_db()
        self.assertEqual(self.nc.capa, [])


class ApplicationAccessTests(QualityAccessBase):
    def setUp(self):
        super().setUp()
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)
        self.app = QualityApplication.objects.create(
            organisation=self.lab_one,
            documents=[{"id": "doc-1", "name": "Accreditation", "status": "verified"}],
        )

    def test_other_laboratory_cannot_assign_or_upload(self):
        self.as_user(self.lab2)
        self.assertEqual(self.client.post(self.url("quality-application-assign", self.app.id)).status_code, 403)
        upload = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        resp = self.client.post(
            self.url("quality-application-upload-document", self.app.id, "doc-1"), {"file": upload}, format="multipart",
        )
        self.assertEqual(resp.status_code, 404)
        self.app.refresh_from_db()
        self.assertIsNone(self.app.assigned_to)
        self.assertEqual(self.app.documents[0]["status"], "verified")

    def test_laboratory_cannot_file_in_another_organisations_name(self):
        self.as_user(self.lab2)
        resp = self.client.post(reverse("quality-application-list"), {"organisation_id": str(self.lab_one.id)}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        created = QualityApplication.objects.get(pk=resp.data["id"])
        self.assertEqual(created.organisation, self.lab_two)

    def test_miner_cannot_file_an_application(self):
        self.as_user(self.miner_a)
        resp = self.client.post(reverse("quality-application-list"), {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_operator_may_file_for_a_named_organisation(self):
        self.as_user(self.operator)
        resp = self.client.post(reverse("quality-application-list"), {"organisation_id": str(self.lab_two.id)}, format="json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(QualityApplication.objects.get(pk=resp.data["id"]).organisation, self.lab_two)

    def test_unverified_applicant_still_reads_its_own_application(self):
        draft_lab = make_org("New Lab", OrganisationType.LABORATORY, status="under_review")
        user = member(draft_lab, "new@lab.test")
        own = QualityApplication.objects.create(organisation=draft_lab)
        self.as_user(user)
        listed = self.client.get(reverse("quality-application-list")).data["results"]
        self.assertEqual([row["id"] for row in listed], [str(own.id)])

    def test_regulator_cannot_change_document_status(self):
        self.as_user(self.regulator)
        resp = self.client.patch(
            self.url("quality-application-document-status", self.app.id, "doc-1"), {"status": "rejected"}, format="json",
        )
        self.assertEqual(resp.status_code, 403)


class AdminIsReadOnlyTests(QualityAccessBase):
    def test_admin_cannot_add_change_or_delete_quality_records(self):
        admin_user = User.objects.create_superuser(email="root@beldium.test", password="Str0ng-Passw0rd!")
        self.client.force_login(admin_user)
        self.assertEqual(self.client.get(reverse("admin:quality_sample_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:quality_sample_delete", args=[self.sample.pk])).status_code, 403)
        resp = self.client.post(reverse("admin:quality_sample_change", args=[self.sample.pk]), {"material": "copper"})
        self.assertEqual(resp.status_code, 403)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.material, "gold")
