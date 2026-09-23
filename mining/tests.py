from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from mining import checklist
from mining.models import (
    Application,
    Inspection,
    LicenceDoc,
    MineSite,
    NonConformity,
    ReviewSection,
    SectionKey,
    SiteStatus,
)
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType


def make_user(email, **extra):
    return User.objects.create_user(email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), **extra)


def make_org(name, org_type):
    return Organisation.objects.create(name=name, organisation_type=org_type, verification_status="verified")


class MiningTestCase(APITestCase):
    """Three audiences over one register, which is what every test needs."""

    def setUp(self):
        self.desk_org = make_org("Beldium Compliance Desk", OrganisationType.COMPLIANCE_PARTNER)
        self.regulator_org = make_org("Minerals Oversight Directorate", OrganisationType.REGULATOR)
        self.miner_org = make_org("Jos Tin Mining Ltd", OrganisationType.MINING_COMPANY)
        self.other_org = make_org("Kaduna Gold Mining Enterprises", OrganisationType.MINING_COMPANY)

        self.operator = make_user("operator@beldium.test")
        OrganisationMembership.objects.create(organisation=self.desk_org, user=self.operator, role=MembershipRole.REVIEWER)

        self.regulator = make_user("regulator@beldium.test")
        OrganisationMembership.objects.create(organisation=self.regulator_org, user=self.regulator, role=MembershipRole.READ_ONLY)

        self.miner = make_user("miner@jos.test")
        OrganisationMembership.objects.create(organisation=self.miner_org, user=self.miner, role=MembershipRole.OWNER)

        self.outsider = make_user("outsider@kaduna.test")
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)

        self.site = MineSite.objects.create(
            organisation=self.miner_org,
            name="Jos Tin Site A",
            mineral="Tin",
            state="Plateau",
            status=SiteStatus.UNDER_REVIEW,
            compliance_score=58,
        )
        ReviewSection.objects.bulk_create(
            [ReviewSection(site=self.site, key=key) for key, _ in SectionKey.choices]
        )

    def complete(self, site):
        """Answer every required prompt on every section."""
        for section in site.sections.all():
            section.fields = [
                {"label": prompt, "value": "Declared"} for prompt in checklist.required_prompts(section.key)
            ]
            section.save()
        return site


class AudienceTests(MiningTestCase):
    def test_capabilities_reflect_membership_not_a_client_claim(self):
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("mining-capabilities"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["audience"], "operator")
        self.assertTrue(response.data["can_decide"])

        self.client.force_authenticate(self.regulator)
        response = self.client.get(reverse("mining-capabilities"))
        self.assertEqual(response.data["audience"], "regulator")
        self.assertFalse(response.data["can_decide"])

        self.client.force_authenticate(self.miner)
        response = self.client.get(reverse("mining-capabilities"))
        self.assertEqual(response.data["audience"], "miner")
        self.assertFalse(response.data["can_decide"])

    def test_user_without_any_membership_is_refused(self):
        self.client.force_authenticate(make_user("nobody@example.test"))
        self.assertEqual(self.client.get(reverse("mining-site-list")).status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(reverse("mining-site-list")).status_code, 401)


class MineSiteScopingTests(MiningTestCase):
    def test_operator_sees_whole_register(self):
        MineSite.objects.create(organisation=self.other_org, name="Kaduna Gold Site A", mineral="Gold", state="Kaduna")
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("mining-site-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_regulator_sees_whole_register_read_only(self):
        self.client.force_authenticate(self.regulator)
        response = self.client.get(reverse("mining-site-list"))
        self.assertEqual(response.data["count"], 1)
        response = self.client.patch(reverse("mining-site-detail", args=[self.site.id]), {"name": "Renamed"})
        self.assertEqual(response.status_code, 403)

    def test_miner_sees_only_own_sites(self):
        MineSite.objects.create(organisation=self.other_org, name="Kaduna Gold Site A", mineral="Gold", state="Kaduna")
        self.client.force_authenticate(self.miner)
        response = self.client.get(reverse("mining-site-list"))
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.site.id))

    def test_outsider_cannot_read_or_write_another_orgs_site(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(reverse("mining-site-detail", args=[self.site.id]))
        self.assertEqual(response.status_code, 404)
        response = self.client.patch(reverse("mining-site-detail", args=[self.site.id]), {"name": "Hijacked"})
        self.assertEqual(response.status_code, 404)

    def test_site_creation_seeds_ten_sections(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-site-list"), {
            "organisation": str(self.miner_org.id), "name": "Jos Tin Site B", "mineral": "Tin", "state": "Plateau",
        })
        self.assertEqual(response.status_code, 201, response.data)
        site = MineSite.objects.get(id=response.data["id"])
        self.assertEqual(site.sections.count(), len(SectionKey.choices))

    def test_miner_cannot_register_site_for_another_organisation(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-site-list"), {
            "organisation": str(self.other_org.id), "name": "Hijack Site", "mineral": "Tin", "state": "Plateau",
        })
        self.assertEqual(response.status_code, 403)


class ReviewSectionTests(MiningTestCase):
    def test_miner_can_save_own_section(self):
        self.client.force_authenticate(self.miner)
        url = reverse("mining-site-section", args=[self.site.id, SectionKey.CORPORATE])
        response = self.client.patch(url, {"fields": [{"label": "Registered name", "value": "Jos Tin Mining Ltd"}]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_outsider_cannot_save_section(self):
        self.client.force_authenticate(self.outsider)
        url = reverse("mining-site-section", args=[self.site.id, SectionKey.CORPORATE])
        response = self.client.patch(url, {"fields": []}, format="json")
        # The site itself is outside the outsider's queryset scope, so the
        # lookup fails before the ownership check is ever reached.
        self.assertEqual(response.status_code, 404)

    def test_only_operator_can_review_a_section(self):
        self.client.force_authenticate(self.miner)
        url = reverse("mining-site-review-section", args=[self.site.id, SectionKey.CORPORATE])
        response = self.client.post(url, {"status": "verified"})
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(self.operator)
        response = self.client.post(url, {"status": "verified", "score": 90})
        self.assertEqual(response.status_code, 200, response.data)
        section = self.site.sections.get(key=SectionKey.CORPORATE)
        self.assertEqual(section.status, "verified")
        self.assertEqual(section.score, 90)

    def test_reviewer_at_an_unverified_compliance_partner_cannot_review_a_section(self):
        """A compliance-partner org only grants desk power once Beldium has
        verified it. Signing up as a compliance partner and adding a
        reviewer does not itself unlock review actions; that self-declared
        organisation_type is worth nothing until an admin verifies the org,
        exactly like a mining company's own verification gate.
        """
        pending_desk_org = Organisation.objects.create(
            name="New Compliance Partner Ltd",
            organisation_type=OrganisationType.COMPLIANCE_PARTNER,
            verification_status="under_review",
        )
        pending_reviewer = make_user("reviewer@new-partner.test")
        OrganisationMembership.objects.create(
            organisation=pending_desk_org, user=pending_reviewer, role=MembershipRole.REVIEWER
        )

        self.client.force_authenticate(pending_reviewer)
        url = reverse("mining-site-review-section", args=[self.site.id, SectionKey.CORPORATE])
        response = self.client.post(url, {"status": "verified", "score": 90})
        self.assertEqual(response.status_code, 403, response.data)


class SiteVerificationOutcomeTests(MiningTestCase):
    def verify(self, key):
        url = reverse("mining-site-review-section", args=[self.site.id, key])
        return self.client.post(url, {"status": "verified"})

    def test_verifying_every_section_makes_the_site_operational(self):
        self.client.force_authenticate(self.operator)
        keys = [key for key, _ in SectionKey.choices]
        for key in keys[:-1]:
            self.assertEqual(self.verify(key).status_code, 200)
        self.site.refresh_from_db()
        self.assertEqual(self.site.status, SiteStatus.UNDER_REVIEW)
        self.assertEqual(self.site.compliance_score, 90)
        self.assertEqual(self.site.risk, "low")

        self.verify(keys[-1])
        self.site.refresh_from_db()
        self.assertEqual(self.site.status, SiteStatus.OPERATIONAL)
        self.assertEqual(self.site.compliance_score, 100)

    def test_full_verification_approves_the_miners_application_and_timeline(self):
        from mining.models import Application

        application = Application.objects.create(organisation=self.miner_org, site=self.site, site_name="Jos Tin Site A")
        self.client.force_authenticate(self.operator)
        for key, _ in SectionKey.choices:
            self.verify(key)
        application.refresh_from_db()
        self.assertEqual(application.status, "approved")

        self.client.force_authenticate(self.miner)
        data = self.client.get(reverse("organisation-timeline", args=[self.miner_org.id])).data
        stages = {s["key"]: s for s in data["stages"]}
        self.assertEqual(stages["site_verification"]["state"], "complete")
        self.assertEqual(data["sites"], {"verified": 1, "total": 1})

    def test_rejecting_a_section_takes_the_site_back_under_review(self):
        self.client.force_authenticate(self.operator)
        for key, _ in SectionKey.choices:
            self.verify(key)
        url = reverse("mining-site-review-section", args=[self.site.id, SectionKey.CORPORATE])
        self.client.post(url, {"status": "rejected", "note": "Certificate expired"})
        self.site.refresh_from_db()
        self.assertEqual(self.site.status, SiteStatus.UNDER_REVIEW)
        self.assertEqual(self.site.compliance_score, 90)


class NonConformityTests(MiningTestCase):
    def test_only_operator_can_raise_a_finding(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-non-conformity-list"), {
            "site": str(self.site.id), "title": "Missing PPE register", "severity": "major",
            "deadline": (timezone.localdate() + timedelta(days=14)).isoformat(),
        })
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(self.operator)
        response = self.client.post(reverse("mining-non-conformity-list"), {
            "site": str(self.site.id), "title": "Missing PPE register", "severity": "major",
            "deadline": (timezone.localdate() + timedelta(days=14)).isoformat(),
        })
        self.assertEqual(response.status_code, 201, response.data)

    def test_miner_can_submit_corrective_action_and_operator_can_close(self):
        finding = NonConformity.objects.create(
            site=self.site, title="Missing PPE register", severity="major",
            deadline=timezone.localdate() + timedelta(days=14),
        )
        self.client.force_authenticate(self.outsider)
        response = self.client.post(reverse("mining-non-conformity-submissions", args=[finding.id]), {"message": "Fixed"})
        # The finding is outside the outsider's queryset scope, so the lookup
        # fails before the ownership check is ever reached.
        self.assertEqual(response.status_code, 404)

        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-non-conformity-submissions", args=[finding.id]), {"message": "PPE register restored"})
        self.assertEqual(response.status_code, 201, response.data)

        self.client.force_authenticate(self.operator)
        response = self.client.post(reverse("mining-non-conformity-close", args=[finding.id]), {"accept": True})
        self.assertEqual(response.status_code, 200, response.data)
        finding.refresh_from_db()
        self.assertEqual(finding.status, NonConformity.Status.CLOSED)

    def test_closing_without_evidence_is_refused(self):
        finding = NonConformity.objects.create(
            site=self.site, title="Missing PPE register", severity="major",
            deadline=timezone.localdate() + timedelta(days=14),
        )
        self.client.force_authenticate(self.operator)
        response = self.client.post(reverse("mining-non-conformity-close", args=[finding.id]), {"accept": True})
        self.assertEqual(response.status_code, 409)


class InspectionTests(MiningTestCase):
    def test_only_operator_can_schedule(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-inspection-list"), {"site": str(self.site.id), "type": "routine"})
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(self.operator)
        response = self.client.post(reverse("mining-inspection-list"), {"site": str(self.site.id), "type": "routine"})
        self.assertEqual(response.status_code, 201, response.data)


class DashboardTests(MiningTestCase):
    def test_dashboard_returns_totals(self):
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("mining-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("totals", response.data)
        self.assertEqual(response.data["totals"]["sites"], 1)

    def test_dashboard_scopes_to_miner_org(self):
        MineSite.objects.create(organisation=self.other_org, name="Kaduna Gold Site A", mineral="Gold", state="Kaduna")
        self.client.force_authenticate(self.miner)
        response = self.client.get(reverse("mining-dashboard"))
        self.assertEqual(response.data["totals"]["sites"], 1)


class ChecklistTests(MiningTestCase):
    def test_checklist_has_ten_sections(self):
        self.client.force_authenticate(self.miner)
        response = self.client.get(reverse("mining-checklist"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["sections"]), len(SectionKey.choices))


class UploadDownloadTests(MiningTestCase):
    def _file(self, name="doc.pdf"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, b"%PDF-1.4 test content", content_type="application/pdf")

    def test_evidence_upload_and_download_round_trip(self):
        self.client.force_authenticate(self.miner)
        section = self.site.sections.get(key=SectionKey.CORPORATE)
        url = reverse("mining-site-section-evidence", args=[self.site.id, SectionKey.CORPORATE])
        response = self.client.post(url, {"name": "CAC Certificate", "file": self._file()}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        evidence_id = response.data["id"]

        download_url = reverse("mining-site-download-evidence", args=[self.site.id, evidence_id])
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)

        self.client.force_authenticate(self.outsider)
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 404)

    def test_licence_upload_and_download_round_trip(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-licence-list"), {
            "site": str(self.site.id), "number": "ML-001", "type": "Mining Lease", "file": self._file("licence.pdf"),
        }, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        licence_id = response.data["id"]

        download_url = reverse("mining-licence-download", args=[licence_id])
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)

    def test_document_upload_review_and_download(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-document-list"), {
            "site": str(self.site.id), "name": "Fire Safety Certificate", "file": self._file("fire.pdf"),
        }, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        document_id = response.data["id"]

        self.client.force_authenticate(self.operator)
        response = self.client.post(reverse("mining-document-review", args=[document_id]), {"status": "verified"})
        self.assertEqual(response.status_code, 200, response.data)

        download_url = reverse("mining-document-download", args=[document_id])
        response = self.client.get(download_url)
        self.assertEqual(response.status_code, 200)


class ApplicationTests(MiningTestCase):
    def test_miner_can_file_application_for_own_org(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-application-list"), {
            "organisation": str(self.miner_org.id), "site_name": "New Site", "mineral": "Tin",
        })
        self.assertEqual(response.status_code, 201, response.data)

    def test_miner_cannot_file_for_another_org(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(reverse("mining-application-list"), {
            "organisation": str(self.other_org.id), "site_name": "New Site", "mineral": "Tin",
        })
        self.assertEqual(response.status_code, 403)


class ApplicationClaimTests(MiningTestCase):
    """Multiple verified compliance-partner orgs see the same register — one
    claiming an application must lock the rest out, since a plain PATCH to
    assigned_to used to let anyone silently overwrite anyone else's claim."""

    def setUp(self):
        super().setUp()
        self.application = Application.objects.create(
            organisation=self.miner_org, site=self.site, site_name=self.site.name, mineral="Tin",
        )
        second_desk = make_org("Second Compliance Partner", OrganisationType.COMPLIANCE_PARTNER)
        self.second_reviewer = make_user("reviewer2@second-desk.test")
        OrganisationMembership.objects.create(
            organisation=second_desk, user=self.second_reviewer, role=MembershipRole.REVIEWER
        )

    def claim_url(self):
        return reverse("mining-application-claim", args=[self.application.id])

    def release_url(self):
        return reverse("mining-application-release", args=[self.application.id])

    def test_reviewer_can_claim_an_unclaimed_application(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 200, response.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.assigned_to_id, self.operator.id)

    def test_a_second_reviewer_cannot_claim_an_already_claimed_application(self):
        self.client.force_authenticate(self.operator)
        self.client.post(self.claim_url())

        self.client.force_authenticate(self.second_reviewer)
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "already_claimed")
        self.application.refresh_from_db()
        self.assertEqual(self.application.assigned_to_id, self.operator.id)

    def test_a_plain_patch_cannot_set_assigned_to(self):
        self.client.force_authenticate(self.operator)
        response = self.client.patch(
            reverse("mining-application-detail", args=[self.application.id]),
            {"assigned_to": str(self.second_reviewer.id)},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.application.refresh_from_db()
        self.assertIsNone(self.application.assigned_to_id)

    def test_miner_cannot_claim_an_application(self):
        self.client.force_authenticate(self.miner)
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 403)

    def test_claimant_can_release_and_another_reviewer_can_then_claim(self):
        self.client.force_authenticate(self.operator)
        self.client.post(self.claim_url())
        release_response = self.client.post(self.release_url())
        self.assertEqual(release_response.status_code, 200, release_response.data)
        self.application.refresh_from_db()
        self.assertIsNone(self.application.assigned_to_id)

        self.client.force_authenticate(self.second_reviewer)
        response = self.client.post(self.claim_url())
        self.assertEqual(response.status_code, 200, response.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.assigned_to_id, self.second_reviewer.id)

    def test_a_reviewer_cannot_release_someone_elses_claim(self):
        self.client.force_authenticate(self.operator)
        self.client.post(self.claim_url())

        self.client.force_authenticate(self.second_reviewer)
        response = self.client.post(self.release_url())
        self.assertEqual(response.status_code, 403)


class OrganisationVerificationTests(MiningTestCase):
    def url(self, decision):
        return reverse("mining-organisation-decision", args=[self.miner_org.id, decision])

    def test_cannot_verify_until_every_site_is_verified(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(self.url("verify"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "organisation_not_ready")

    def test_desk_verifies_ready_organisation_and_miner_dashboard_flips(self):
        self.client.force_authenticate(self.operator)
        for key, _ in SectionKey.choices:
            self.client.post(reverse("mining-site-review-section", args=[self.site.id, key]), {"status": "verified"})
        listing = self.client.get(reverse("mining-organisation-verification")).data["results"]
        row = next(r for r in listing if r["id"] == str(self.miner_org.id))
        self.assertTrue(row["ready"])

        response = self.client.post(self.url("verify"))
        self.assertEqual(response.status_code, 200, response.data)
        self.miner_org.refresh_from_db()
        self.assertEqual(self.miner_org.verification_status, "verified")
        self.assertIsNotNone(self.miner_org.submitted_at)

    def test_miner_cannot_verify_own_organisation(self):
        self.client.force_authenticate(self.miner)
        self.assertEqual(self.client.post(self.url("verify")).status_code, 403)

    def test_reject_needs_reason(self):
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.post(self.url("reject")).status_code, 400)
        response = self.client.post(self.url("reject"), {"reason": "Licence not supplied"})
        self.assertEqual(response.status_code, 200)
        self.miner_org.refresh_from_db()
        self.assertEqual(self.miner_org.verification_status, "rejected")
