import hashlib
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from vault.models import AppendOnlyError, VaultAction, VaultActivity, VaultApproval, VaultCategory, VaultDocument

MEDIA_ROOT = tempfile.mkdtemp(prefix="vault-tests-")


def make_user(email, **extra):
    return User.objects.create_user(
        email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), **extra,
    )


def make_org(name, org_type):
    return Organisation.objects.create(
        name=name, organisation_type=org_type,
        registration_number=name.upper().replace(" ", "-"), verification_status="verified",
    )


def pdf(content=b"%PDF-1.4 sample", name="report.pdf"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class VaultTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.operator = make_user("operator@vault.test", is_staff=True)
        self.second_operator = make_user("second@vault.test", is_staff=True)
        self.partner = make_user("partner@vault.test")
        self.other_partner = make_user("other@vault.test")
        self.miner = make_user("miner@vault.test")
        self.regulator = make_user("regulator@vault.test")
        self.nobody = make_user("nobody@vault.test")

        self.lab = make_org("Assay Labs", OrganisationType.LABORATORY)
        self.other_lab = make_org("Rival Labs", OrganisationType.LABORATORY)
        self.mine = make_org("Ilesa Mining", OrganisationType.MINING_COMPANY)
        self.regulator_org = make_org("Mines Regulator", OrganisationType.REGULATOR)
        for org, user in [
            (self.lab, self.partner), (self.other_lab, self.other_partner),
            (self.mine, self.miner), (self.regulator_org, self.regulator),
        ]:
            OrganisationMembership.objects.create(organisation=org, user=user, role=MembershipRole.OWNER)

        self.documents_url = reverse("vault-document-list")

    def upload(self, user, file=None, **fields):
        self.client.force_authenticate(user)
        data = {"sector": "quality", "category": "laboratory-reports", "title": "Assay report", "file": file or pdf()}
        data.update(fields)
        return self.client.post(self.documents_url, data, format="multipart")

    def detail_url(self, document_id, name="detail"):
        return reverse(f"vault-document-{name}", args=[document_id])

    # --- categories ---------------------------------------------------------

    def test_quality_categories_are_seeded(self):
        codes = list(VaultCategory.objects.filter(sector="quality").values_list("code", flat=True))
        self.assertIn("laboratory-reports", codes)
        self.assertIn("other", codes)

    def test_sectors_lists_quality_for_partner_and_desk_only(self):
        for user, audience in [(self.partner, "member"), (self.operator, "desk")]:
            self.client.force_authenticate(user)
            response = self.client.get(reverse("vault-sectors"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual([x["sector"] for x in response.data["sectors"]], ["quality"])
            self.assertEqual(response.data["sectors"][0]["audience"], audience)
        for user in [self.miner, self.regulator, self.nobody]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(reverse("vault-sectors")).data["sectors"], [])

    def test_partner_needs_an_approved_organisation(self):
        document = self.upload(self.partner).data["id"]
        for status_value in ["draft", "under_review", "rejected", "suspended"]:
            Organisation.objects.filter(pk=self.lab.pk).update(verification_status=status_value)
            self.client.force_authenticate(self.partner)
            self.assertEqual(self.client.get(reverse("vault-sectors")).data["sectors"], [], status_value)
            self.assertEqual(self.client.get(self.documents_url, {"sector": "quality"}).data["count"], 0)
            self.assertEqual(self.client.get(self.detail_url(document)).status_code, 404)
            self.assertEqual(self.upload(self.partner, file=pdf(b"new")).status_code, 403)

    def test_sector_without_a_vault_is_refused(self):
        self.client.force_authenticate(self.operator)
        response = self.client.get(self.documents_url, {"sector": "logistics"})
        self.assertEqual(response.status_code, 404)
        response = self.upload(self.operator, sector="logistics")
        self.assertEqual(response.status_code, 404)

    # --- upload -------------------------------------------------------------

    def test_partner_upload_is_filed_under_own_organisation_with_fingerprint(self):
        content = b"%PDF-1.4 lab report"
        response = self.upload(self.partner, file=pdf(content))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["organisation"]["name"], "Assay Labs")
        self.assertEqual(response.data["status"], "awaiting_approval")
        self.assertEqual(response.data["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(response.data["version"], 1)
        document = VaultDocument.objects.get(pk=response.data["id"])
        self.assertTrue(VaultActivity.objects.filter(document=document, action=VaultAction.UPLOADED).exists())

    def test_partner_cannot_file_for_another_organisation(self):
        response = self.upload(self.partner, organisation=str(self.other_lab.pk))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(VaultDocument.objects.exists())

    def test_desk_can_file_for_beldium_or_for_an_organisation(self):
        response = self.upload(self.operator)
        self.assertEqual(response.data["organisation"], {"id": None, "name": "Beldium"})
        response = self.upload(
            self.operator, file=pdf(b"other"), organisation=str(self.lab.pk),
            received_from="email", received_date="2026-11-10",
        )
        self.assertEqual(response.data["organisation"]["name"], "Assay Labs")

    def test_wrong_roles_cannot_upload(self):
        for user in [self.miner, self.regulator, self.nobody]:
            self.assertEqual(self.upload(user).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.documents_url, {}).status_code, 401)

    def test_unknown_category_and_bad_file_type_are_rejected(self):
        self.assertEqual(self.upload(self.partner, category="nonsense").status_code, 400)
        script = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
        self.assertEqual(self.upload(self.partner, file=script).status_code, 400)

    def test_same_file_twice_is_refused(self):
        self.assertEqual(self.upload(self.partner).status_code, 201)
        response = self.upload(self.partner, title="Copy")
        self.assertEqual(response.status_code, 409)

    # --- reading ------------------------------------------------------------

    def test_partner_sees_only_own_documents(self):
        mine = self.upload(self.partner).data["id"]
        theirs = self.upload(self.other_partner, file=pdf(b"theirs")).data["id"]

        self.client.force_authenticate(self.partner)
        listed = self.client.get(self.documents_url, {"sector": "quality"}).data["results"]
        self.assertEqual([d["id"] for d in listed], [mine])
        self.assertEqual(self.client.get(self.detail_url(theirs)).status_code, 404)
        self.assertEqual(self.client.get(self.detail_url(theirs, "download")).status_code, 404)

        self.client.force_authenticate(self.operator)
        listed = self.client.get(self.documents_url, {"sector": "quality"}).data["results"]
        self.assertEqual({d["id"] for d in listed}, {mine, theirs})

    def test_wrong_roles_cannot_read(self):
        document = self.upload(self.partner).data["id"]
        for user in [self.miner, self.regulator, self.nobody]:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.detail_url(document)).status_code, 404)
            self.assertEqual(self.client.get(self.documents_url, {"sector": "quality"}).data["count"], 0)

    def test_list_filters_by_category_and_status(self):
        self.upload(self.partner)
        self.upload(self.partner, file=pdf(b"sop"), category="procedures", title="Sampling SOP")
        self.client.force_authenticate(self.partner)
        listed = self.client.get(self.documents_url, {"sector": "quality", "category": "procedures"}).data
        self.assertEqual([d["title"] for d in listed["results"]], ["Sampling SOP"])
        listed = self.client.get(self.documents_url, {"sector": "quality", "status": "approved"}).data
        self.assertEqual(listed["count"], 0)

    def test_download_returns_file_and_is_logged(self):
        document = self.upload(self.partner, file=pdf(b"%PDF download me")).data["id"]
        self.client.force_authenticate(self.partner)
        response = self.client.get(self.detail_url(document, "download"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF download me")
        self.assertTrue(VaultActivity.objects.filter(document_id=document, action=VaultAction.DOWNLOADED, actor=self.partner).exists())

    def test_activity_is_shown_to_the_desk_only(self):
        document = self.upload(self.partner).data["id"]
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.get(self.detail_url(document)).data["activity"], [])
        self.client.force_authenticate(self.operator)
        self.assertEqual(len(self.client.get(self.detail_url(document)).data["activity"]), 1)

    # --- versions -----------------------------------------------------------

    def test_new_version_replaces_the_listed_document_and_keeps_history(self):
        first = self.upload(self.partner).data["id"]
        self.client.force_authenticate(self.partner)
        response = self.client.post(
            self.detail_url(first, "versions"), {"file": pdf(b"v2"), "note": "Corrected totals"}, format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["version"], 2)
        second = response.data["id"]

        listed = self.client.get(self.documents_url, {"sector": "quality"}).data["results"]
        self.assertEqual([d["id"] for d in listed], [second])
        detail = self.client.get(self.detail_url(second)).data
        self.assertEqual([v["version"] for v in detail["versions"]], [2, 1])
        self.assertTrue(VaultDocument.objects.filter(pk=first).exists())

        # A superseded version cannot be replaced a second time.
        response = self.client.post(self.detail_url(first, "versions"), {"file": pdf(b"v3")}, format="multipart")
        self.assertEqual(response.status_code, 409)

    def test_identical_version_is_refused(self):
        first = self.upload(self.partner, file=pdf(b"same")).data["id"]
        self.client.force_authenticate(self.partner)
        response = self.client.post(self.detail_url(first, "versions"), {"file": pdf(b"same")}, format="multipart")
        self.assertEqual(response.status_code, 409)

    def test_other_owner_cannot_add_a_version(self):
        first = self.upload(self.partner).data["id"]
        self.client.force_authenticate(self.other_partner)
        response = self.client.post(self.detail_url(first, "versions"), {"file": pdf(b"x")}, format="multipart")
        self.assertEqual(response.status_code, 404)

    # --- approval -----------------------------------------------------------

    def test_second_person_on_the_desk_approves(self):
        document = self.upload(self.operator).data["id"]

        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.post(self.detail_url(document, "approve")).status_code, 403)

        self.client.force_authenticate(self.second_operator)
        response = self.client.post(self.detail_url(document, "approve"), {"note": "Checked against original"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(self.client.post(self.detail_url(document, "approve")).status_code, 409)

    def test_partner_cannot_approve(self):
        document = self.upload(self.partner).data["id"]
        self.client.force_authenticate(self.partner)
        self.assertEqual(self.client.post(self.detail_url(document, "approve")).status_code, 403)
        self.client.force_authenticate(self.other_partner)
        self.assertEqual(self.client.post(self.detail_url(document, "approve")).status_code, 404)
        self.assertFalse(VaultApproval.objects.exists())

    # --- integrity ----------------------------------------------------------

    def test_integrity_check_reports_intact_and_tampered_copies(self):
        document_id = self.upload(self.partner).data["id"]
        self.client.force_authenticate(self.partner)
        response = self.client.post(self.detail_url(document_id, "verify"))
        self.assertTrue(response.data["intact"])

        document = VaultDocument.objects.get(pk=document_id)
        with open(document.file.path, "wb") as stored:
            stored.write(b"tampered")
        response = self.client.post(self.detail_url(document_id, "verify"))
        self.assertFalse(response.data["intact"])
        self.assertEqual(
            VaultActivity.objects.filter(document=document, action=VaultAction.INTEGRITY_CHECKED).count(), 2,
        )

    # --- append only ----------------------------------------------------------

    def test_api_has_no_update_or_delete_route(self):
        document = self.upload(self.partner).data["id"]
        self.client.force_authenticate(self.operator)
        for method in ["put", "patch", "delete"]:
            response = getattr(self.client, method)(self.detail_url(document), {"title": "x"})
            self.assertEqual(response.status_code, 405)

    def test_orm_refuses_updates_and_deletes(self):
        document = VaultDocument.objects.get(pk=self.upload(self.partner).data["id"])
        document.title = "Changed"
        with self.assertRaises(AppendOnlyError):
            document.save()
        with self.assertRaises(AppendOnlyError):
            document.delete()
        with self.assertRaises(AppendOnlyError):
            VaultDocument.objects.filter(pk=document.pk).update(title="Changed")
        with self.assertRaises(AppendOnlyError):
            VaultDocument.objects.all().delete()
        with self.assertRaises(AppendOnlyError):
            VaultActivity.objects.all().delete()
