import shutil
import tempfile

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import User
from export.models import Exporter
from logistics.models import LogisticsAccessGrant, LogisticsCompany
from mining.models import MineSite, MiningAccessGrant
from onboarding.models import OnboardingDashboard, OnboardingOption, ParticipantApplication, RegulatorAccessRequest
from onboarding.participants import REQUIREMENTS
from organisations import rights
from organisations.models import (
    JoinRequest,
    MembershipRole,
    Organisation,
    OrganisationMembership,
    OrganisationType,
    SectorAccessGrant,
)

MEDIA = tempfile.mkdtemp()


def make_user(email, **extra):
    return User.objects.create_user(email, "password", **extra)


def pdf(name="file.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 test", content_type="application/pdf")


def error_code(response):
    return (response.data.get("error") or {}).get("code")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    MEDIA_ROOT=MEDIA,
    MINER_PORTAL_URL="https://miners.example.test",
    LOGISTICS_PORTAL_URL="https://logistics.example.test",
)
class OnboardingTestCase(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()


class OptionsTests(OnboardingTestCase):
    url = reverse("onboarding-options")

    def test_anyone_can_read_the_options(self):
        response = self.client.get(self.url, {"sector": "processing"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sector"], "processing")
        self.assertEqual([d["key"] for d in response.data["dashboards"]], ["applicant", "partner"])
        applicant = response.data["dashboards"][0]
        self.assertEqual(applicant["label"], "Processing company")
        self.assertEqual([o["key"] for o in applicant["options"]], ["register", "join", "regulatory"])
        register = applicant["options"][0]
        self.assertEqual(register["kind"], "register")
        self.assertEqual(register["role"], "participant")
        self.assertEqual(register["org_path"], "new")
        self.assertNotIn("external_url", register)
        # The regulatory option has no org_path, so the key is left out.
        self.assertNotIn("org_path", applicant["options"][2])

    def test_every_sector_is_seeded(self):
        for sector in ["mining", "processing", "warehousing", "export", "marketplace", "logistics"]:
            response = self.client.get(self.url, {"sector": sector})
            kinds = [o["kind"] for d in response.data["dashboards"] for o in d["options"]]
            for kind in ("register", "join", "regulatory"):
                self.assertIn(kind, kinds, sector)
        quality = self.client.get(self.url, {"sector": "quality"}).data["dashboards"]
        self.assertEqual([d["key"] for d in quality], ["partner"])
        self.assertEqual(len(quality[0]["options"]), 6)

    def test_mining_shows_only_the_compliance_partner_side(self):
        # Miners register on the Miner Portal, not on the compliance app.
        dashboards = self.client.get(self.url, {"sector": "mining"}).data["dashboards"]
        self.assertEqual([d["key"] for d in dashboards], ["partner"])

    def test_portal_links_come_from_settings(self):
        # The mining applicant side is switched off, not deleted; switched
        # back on it still links to the Miner Portal from settings.
        OnboardingDashboard.objects.filter(sector="mining", key="applicant").update(enabled=True)
        mining = self.client.get(self.url, {"sector": "mining"}).data["dashboards"][0]["options"][0]
        self.assertEqual(mining["external_url"], "https://miners.example.test")
        self.assertEqual(mining["external_label"], "Continue on the Miner Portal")
        logistics = self.client.get(self.url, {"sector": "logistics"}).data["dashboards"][0]["options"]
        self.assertEqual([o["key"] for o in logistics][-1], "driver")
        self.assertEqual(logistics[-1]["external_url"], "https://logistics.example.test")

    def test_disabled_options_are_hidden_and_dashboard_filter(self):
        OnboardingOption.objects.filter(dashboard__sector="export", key="independent").update(enabled=False)
        response = self.client.get(self.url, {"sector": "export", "dashboard": "partner"})
        self.assertEqual(len(response.data["dashboards"]), 1)
        self.assertNotIn("independent", [o["key"] for o in response.data["dashboards"][0]["options"]])

    def test_unknown_sector_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)
        self.assertEqual(self.client.get(self.url, {"sector": "fishing"}).status_code, 400)


def complete_payload(sector):
    rules = REQUIREMENTS[sector]
    operations = {key: "Answer" for key in rules["operations"]}
    if sector == "marketplace":
        operations.update({"participantType": "OEM", "sanctionsConsent": True})
    return {
        "company": {
            "legalName": "Jos Processing Ltd", "rcNumber": "RC-1234567", "tin": "TIN-1", "address": "1 Road, Jos",
            "email": "info@jos.test", "phone": "08000000000", "state": "Plateau",
        },
        "operations": operations,
        "sites": [{"name": "Plant A", "state": "Plateau", "capacity": 100}],
        "representative": {
            "fullName": "Ada Obi", "jobTitle": "Director", "email": "ada@jos.test", "phone": "0800",
            "authorised": "yes", "signature": "Ada Obi",
        },
        "declarations": {key: True for key in rules["declarations"]},
    }


class ParticipantTests(OnboardingTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("owner@jos.test", first_name="Ada", last_name="Obi")
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("participant-application-list"), {"sector": "processing", "name": "Jos Processing Ltd"})
        self.assertEqual(response.status_code, 201, response.data)
        self.application = ParticipantApplication.objects.get(pk=response.data["id"])
        self.org = self.application.organisation
        self.staff = make_user("staff@jos.test")
        OrganisationMembership.objects.create(organisation=self.org, user=self.staff, role=MembershipRole.MEMBER)
        self.outsider = make_user("outsider@kano.test")

    def section(self, user, name, data):
        self.client.force_authenticate(user)
        return self.client.put(
            reverse("participant-application-save-section", args=[self.application.id, name]), data, format="json",
        )

    def upload(self, user, title):
        self.client.force_authenticate(user)
        return self.client.post(
            reverse("participant-application-documents", args=[self.application.id]),
            {"title": title, "file": pdf()}, format="multipart",
        )

    def submit(self, user):
        self.client.force_authenticate(user)
        return self.client.post(reverse("participant-application-submit", args=[self.application.id]))

    def fill(self):
        for name, data in complete_payload("processing").items():
            self.assertEqual(self.section(self.owner, name, data).status_code, 200)
        for title, required in REQUIREMENTS["processing"]["documents"]:
            if required:
                self.assertEqual(self.upload(self.owner, title).status_code, 201)

    def test_register_makes_the_caller_the_active_owner(self):
        self.assertEqual(self.org.organisation_type, OrganisationType.PROCESSING_COMPANY)
        membership = OrganisationMembership.objects.get(organisation=self.org, user=self.owner)
        self.assertEqual(membership.role, MembershipRole.OWNER)
        self.assertTrue(membership.is_active)
        self.assertEqual(self.application.status, "draft")
        self.assertEqual(self.org.verification_status, "draft")

    def test_one_company_per_sector(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("participant-application-list"), {"sector": "processing", "name": "Second Ltd"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(error_code(response), "already_in_sector")
        # Another sector is fine.
        response = self.client.post(reverse("participant-application-list"), {"sector": "export", "name": "Jos Exports"})
        self.assertEqual(response.status_code, 201)

    def test_marketplace_needs_a_subtype(self):
        buyer = make_user("buyer@oem.test")
        self.client.force_authenticate(buyer)
        url = reverse("participant-application-list")
        self.assertEqual(self.client.post(url, {"sector": "marketplace", "name": "OEM Ltd"}).status_code, 400)
        response = self.client.post(url, {"sector": "marketplace", "name": "OEM Ltd", "participant_subtype": "oem"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["organisation"]["organisation_type"], "market_participant")
        self.assertEqual(response.data["organisation"]["participant_subtype"], "oem")

    def test_mining_and_logistics_register_elsewhere(self):
        user = make_user("miner@jos.test")
        self.client.force_authenticate(user)
        response = self.client.post(reverse("participant-application-list"), {"sector": "mining", "name": "Mine Ltd"})
        self.assertEqual(response.status_code, 400)

    def test_staff_can_edit_drafts_and_upload(self):
        self.assertEqual(self.section(self.staff, "operations", {"minerals": ["Tin (Cassiterite)"], "designCapacity": 50}).status_code, 200)
        self.assertEqual(self.section(self.staff, "sites", [{"name": "Plant A"}]).status_code, 200)
        self.assertEqual(self.upload(self.staff, "Mineral Processing Licence").status_code, 201)

    def test_staff_cannot_edit_profile_sign_or_submit(self):
        for name in ("company", "representative", "declarations"):
            data = complete_payload("processing")[name]
            self.assertEqual(self.section(self.staff, name, data).status_code, 403, name)
        self.fill()
        self.assertEqual(self.submit(self.staff).status_code, 403)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "draft")

    def test_outsider_and_pending_member_are_refused(self):
        pending = make_user("pending@jos.test")
        JoinRequest.objects.create(organisation=self.org, requester=pending)
        for user in (self.outsider, pending):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(reverse("participant-application-detail", args=[self.application.id])).status_code, 404)
            self.assertEqual(self.section(user, "operations", {"minerals": ["Gold"]}).status_code, 404)
            self.assertEqual(self.upload(user, "Mineral Processing Licence").status_code, 404)
            self.assertEqual(self.submit(user).status_code, 404)
            listed = self.client.get(reverse("participant-application-list")).data
            self.assertEqual(listed["count"] if isinstance(listed, dict) else len(listed), 0)

    def test_section_shape_is_checked(self):
        self.assertEqual(self.section(self.owner, "sites", {"name": "not a list"}).status_code, 400)
        self.assertEqual(self.section(self.owner, "declarations", {"accurate": "yes"}).status_code, 400)
        self.assertEqual(self.section(self.owner, "operations", {"minerals": {"nested": 1}}).status_code, 400)
        self.client.force_authenticate(self.owner)
        response = self.client.put(reverse("participant-application-save-section", args=[self.application.id, "secret"]), {}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_unknown_document_title_is_refused(self):
        self.assertEqual(self.upload(self.owner, "Something else").status_code, 400)

    def test_incomplete_application_cannot_be_submitted(self):
        response = self.submit(self.owner)
        self.assertEqual(response.status_code, 400)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "draft")

    def test_admin_submits_a_complete_application(self):
        self.fill()
        self.client.force_authenticate(self.owner)
        detail = self.client.get(reverse("participant-application-detail", args=[self.application.id])).data
        self.assertEqual(detail["missing"], [])
        self.assertTrue(detail["can_submit"])
        response = self.submit(self.owner)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "submitted")
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status, "under_review")
        self.assertEqual(self.org.registration_number, "RC-1234567")
        # A submitted application is closed to edits.
        self.assertEqual(self.section(self.owner, "operations", {"minerals": ["Gold"]}).status_code, 409)


class RegulatorTests(OnboardingTestCase):
    def setUp(self):
        super().setUp()
        self.requester = make_user("officer@mines.gov.test", first_name="Kemi")
        self.operator = make_user("desk@beldium.test", is_staff=True)
        self.miner_org = Organisation.objects.create(name="Jos Tin", organisation_type=OrganisationType.MINING_COMPANY, verification_status="verified")
        self.site = MineSite.objects.create(organisation=self.miner_org, name="Site A", mineral="Tin", state="Plateau")
        haulier = Organisation.objects.create(name="Jos Haulage", organisation_type=OrganisationType.LOGISTICS_COMPANY)
        self.company = LogisticsCompany.objects.create(organisation=haulier, contact_name="A", contact_email="a@h.test", contact_phone="0800")
        exporter_org = Organisation.objects.create(name="Jos Exports", organisation_type=OrganisationType.EXPORTER)
        self.exporter = Exporter.objects.create(organisation=exporter_org, contact_name="B", contact_email="b@e.test", contact_phone="0800")

    def create(self, user=None, **overrides):
        self.client.force_authenticate(user or self.requester)
        data = {
            "agency_name": "Mines Inspectorate Department", "institution_type": "Federal Agency / Parastatal",
            "official_email": "officer@mines.gov.test", "office": "Zonal office, Jos", "state": "Plateau",
            "requester_title": "Assistant Director", "requested_sectors": ["mining", "logistics", "export"],
            "mandate": "Oversight",
        }
        data.update(overrides)
        return self.client.post(reverse("regulator-request-list"), data, format="json")

    def approve(self, access_request_id, sectors, user=None):
        self.client.force_authenticate(user or self.operator)
        return self.client.post(reverse("regulator-request-approve", args=[access_request_id]), {"sectors": sectors}, format="json")

    def test_request_is_created_pending(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["requested_sectors"], ["mining", "export", "logistics"])
        self.assertEqual(self.create().status_code, 409)
        mine = self.client.get(reverse("regulator-request-mine"))
        self.assertEqual(len(mine.data), 1)

    def test_anonymous_cannot_ask(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(reverse("regulator-request-list"), {}).status_code, 401)

    def test_free_mail_needs_a_letter(self):
        response = self.create(official_email="officer@gmail.com")
        self.assertEqual(response.status_code, 400)
        self.client.force_authenticate(self.requester)
        response = self.client.post(reverse("regulator-request-list"), {
            "agency_name": "Mines Inspectorate", "official_email": "officer@gmail.com", "office": "Jos",
            "state": "Plateau", "requested_sectors": "mining,logistics", "authority_letter": pdf("letter.pdf"),
        }, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["has_authority_letter"])
        self.assertEqual(response.data["requested_sectors"], ["mining", "logistics"])
        letter = self.client.get(reverse("regulator-request-letter", args=[response.data["id"]]))
        self.assertEqual(letter.status_code, 200)

    def test_only_operators_list_approve_or_reject(self):
        request_id = self.create().data["id"]
        self.client.force_authenticate(self.requester)
        self.assertEqual(self.client.get(reverse("regulator-request-list")).status_code, 403)
        self.assertEqual(self.approve(request_id, ["mining"], user=self.requester).status_code, 403)
        response = self.client.post(reverse("regulator-request-reject", args=[request_id]), {"reason": "No"})
        self.assertEqual(response.status_code, 403)
        other = make_user("other@x.test")
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(reverse("regulator-request-detail", args=[request_id])).status_code, 404)
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get(reverse("regulator-request-list")).status_code, 200)

    def test_only_requested_sectors_can_be_approved(self):
        request_id = self.create(requested_sectors=["mining"]).data["id"]
        self.assertEqual(self.approve(request_id, ["processing"]).status_code, 400)
        self.assertEqual(self.approve(request_id, []).status_code, 400)

    def test_approval_creates_the_agency_and_its_grants(self):
        request_id = self.create().data["id"]
        response = self.approve(request_id, ["mining", "logistics"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(response.data["approved_sectors"], ["mining", "logistics"])
        org = Organisation.objects.get(pk=response.data["organisation"])
        self.assertEqual(org.organisation_type, OrganisationType.REGULATOR)
        self.assertEqual(org.verification_status, "verified")
        membership = OrganisationMembership.objects.get(organisation=org, user=self.requester)
        self.assertEqual(membership.role, MembershipRole.OWNER)
        self.assertEqual(set(SectorAccessGrant.objects.filter(organisation=org).values_list("sector", flat=True)), {"mining", "logistics"})
        self.assertTrue(LogisticsAccessGrant.objects.filter(company=self.company, user=self.requester, role="regulator").exists())
        self.assertTrue(MiningAccessGrant.objects.filter(organisation=self.miner_org, user=self.requester, role="regulator").exists())
        self.assertEqual(self.approve(request_id, ["mining"]).status_code, 409)

    def test_approved_regulator_reads_only_granted_sectors_and_writes_nothing(self):
        request_id = self.create().data["id"]
        self.approve(request_id, ["mining"])
        self.client.force_authenticate(self.requester)
        response = self.client.get(reverse("mining-site-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(self.client.get(reverse("mining-capabilities")).data["audience"], "regulator")
        # Writes are refused.
        self.assertEqual(self.client.patch(reverse("mining-site-detail", args=[self.site.id]), {"name": "X"}).status_code, 403)
        self.assertIn(self.client.post(reverse("mining-site-list"), {"name": "New", "mineral": "Tin", "state": "Plateau"}).status_code, {403})
        # Sectors that were not granted show nothing.
        self.assertEqual(self.client.get(reverse("processor-list")).status_code, 403)
        exporters = self.client.get(reverse("export-exporter-list")).data
        self.assertEqual(exporters["count"] if isinstance(exporters, dict) else len(exporters), 0)
        me = self.client.get(reverse("mining-capabilities")).data
        self.assertEqual(me["dashboard"], "regulator")
        self.assertEqual(me["rights"], rights.REGULATOR_ADMIN_RIGHTS)
        self.assertEqual(me["granted_sectors"], ["mining"])

    def test_export_grant_reads_the_register_but_cannot_write(self):
        request_id = self.create().data["id"]
        self.approve(request_id, ["export"])
        self.client.force_authenticate(self.requester)
        exporters = self.client.get(reverse("export-exporter-list")).data
        self.assertEqual(exporters["count"] if isinstance(exporters, dict) else len(exporters), 1)
        response = self.client.patch(reverse("export-exporter-detail", args=[self.exporter.id]), {"contact_name": "X"})
        self.assertEqual(response.status_code, 403)
        # Mining was not granted.
        self.assertEqual(self.client.get(reverse("mining-site-list")).status_code, 403)

    def test_unapproved_regulator_sees_nothing(self):
        self.create()
        self.client.force_authenticate(self.requester)
        self.assertEqual(self.client.get(reverse("mining-site-list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("processor-list")).status_code, 403)
        me = self.client.get(reverse("onboarding-me"), {"sector": "mining"}).data
        self.assertEqual(me["membership_status"], "pending")
        self.assertEqual(me["rights"], [])
        self.assertEqual(me["regulator_request"]["status"], "pending")

    def test_reject_needs_a_reason(self):
        request_id = self.create().data["id"]
        self.client.force_authenticate(self.operator)
        url = reverse("regulator-request-reject", args=[request_id])
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        response = self.client.post(url, {"reason": "Not an agency email."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(response.data["rejection_reason"], "Not an agency email.")
        self.assertFalse(Organisation.objects.filter(organisation_type=OrganisationType.REGULATOR).exists())
        self.assertEqual(RegulatorAccessRequest.objects.get(pk=request_id).status, "rejected")

    def test_revoked_grant_leaves_nothing_visible(self):
        request_id = self.create().data["id"]
        self.approve(request_id, ["mining"])
        SectorAccessGrant.objects.update(is_active=False)
        self.client.force_authenticate(self.requester)
        self.assertEqual(self.client.get(reverse("mining-site-list")).status_code, 403)


class MeTests(OnboardingTestCase):
    def setUp(self):
        super().setUp()
        self.org = Organisation.objects.create(name="Jos Processing", organisation_type=OrganisationType.PROCESSING_COMPANY)
        self.owner = make_user("owner@jos.test", first_name="Ada", last_name="Obi")
        self.staff = make_user("staff@jos.test")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role=MembershipRole.OWNER)
        self.staff_m = OrganisationMembership.objects.create(organisation=self.org, user=self.staff, role=MembershipRole.MEMBER)

    def me(self, user, sector="processing", name="onboarding-me"):
        self.client.force_authenticate(user)
        if name == "onboarding-me":
            return self.client.get(reverse(name), {"sector": sector})
        return self.client.get(reverse(name))

    def test_admin(self):
        data = self.me(self.owner).data
        self.assertEqual(data["membership_status"], "active")
        self.assertEqual(data["membership_role"], "owner")
        self.assertEqual(data["dashboard"], "applicant")
        self.assertEqual(data["rights"], rights.ADMIN_RIGHTS)
        self.assertEqual(data["organisation"]["id"], str(self.org.id))
        self.assertEqual(data["user"]["email"], "owner@jos.test")

    def test_staff(self):
        data = self.me(self.staff).data
        self.assertEqual(data["rights"], rights.STAFF_RIGHTS)
        self.assertNotIn(rights.APPLICATION_SUBMIT, data["rights"])
        self.assertNotIn(rights.FINANCE_VIEW, data["rights"])
        self.assertEqual(data["admin_name"], "Ada Obi")

    def test_pending_member_gets_the_name_only(self):
        pending = make_user("pending@jos.test")
        JoinRequest.objects.create(organisation=self.org, requester=pending)
        data = self.me(pending).data
        self.assertEqual(data["membership_status"], "pending")
        self.assertEqual(data["rights"], [])
        self.assertEqual(set(data["organisation"]), {"id", "name"})
        self.assertEqual(data["admin_name"], "Ada Obi")
        self.assertIsNotNone(data["pending_join_request"])

    def test_suspended_member_has_no_rights(self):
        self.staff_m.is_active = False
        self.staff_m.save()
        data = self.me(self.staff).data
        self.assertEqual(data["membership_status"], "suspended")
        self.assertEqual(data["rights"], [])

    def test_wrong_sector_is_none(self):
        data = self.me(self.owner, sector="export").data
        self.assertEqual(data["membership_status"], "none")
        self.assertIsNone(data["organisation"])

    def test_sector_me_endpoints_carry_the_body(self):
        pending = make_user("pending2@jos.test")
        JoinRequest.objects.create(organisation=self.org, requester=pending)
        response = self.me(pending, name="processing-capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["membership_status"], "pending")
        for name in ["processing-capabilities", "mining-capabilities", "export-me", "warehousing-me",
                     "marketplace-me", "logistics-me", "quality-me"]:
            response = self.me(self.owner, name=name)
            self.assertEqual(response.status_code, 200, name)
            for key in ("user", "organisation", "membership_role", "membership_status", "dashboard", "rights"):
                self.assertIn(key, response.data, name)
        self.assertEqual(self.me(self.owner, name="processing-capabilities").data["rights"], rights.ADMIN_RIGHTS)
        self.assertEqual(self.me(self.owner, name="export-me").data["membership_status"], "none")
