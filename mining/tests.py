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
