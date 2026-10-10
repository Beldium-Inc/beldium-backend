import json
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import ComplianceApplication
from organisations.models import Organisation, OrganisationMembership
from quality.models import QualityApplication, QualityProfessionalApplication

SECTIONS = {
    "organisation": {
        "legal_name": "Jos Assay Ltd", "trading_name": "Jos Assay", "organisation_type": "Assay Organisation",
        "registration_number": "RC-1234567", "tax_identifier": "12345678-0001",
        "registered_address": "1 Rayfield Road, Jos", "country": "Nigeria", "website": "https://josassay.test",
    },
    "services": {"capabilities": ["Assay"], "minerals": ["Tin (Cassiterite)"]},
    "laboratories": {"laboratories": [{"name": "Jos Central Laboratory", "location": "Jos, Plateau", "registration_number": "LAB-NG-0412"}]},
    "accreditation": {
        "accreditation_body": "NiNAS", "accreditation_number": "TL-0042", "accreditation_expiry": "2027-10-10",
        "standard": "ISO/IEC 17025:2017", "accredited_scope": "Au by FA-AAS in ores",
    },
    "testing-methods": {"testing_methods": ["ICP-OES", "XRF"]},
    "equipment": {"equipment": [{"name": "Agilent 5110 ICP-OES", "serial_number": "MY21480012", "calibration_date": ""}]},
    "personnel": {"personnel": [{"full_name": "Dr. Ngozi Eze", "role": "Quality Manager", "email": "ngozi@lab.ng"}]},
    "sampling": {
        "geographic_coverage": ["North Central Nigeria"], "field_sampling_teams": 3,
        "tamper_evident_sealing": "Numbered seals", "sampling_procedure_summary": "",
    },
    "declaration": {
        "information_true": True, "consent_to_verification": True,
        "understands_verification": True, "signature": "Ngozi Eze",
    },
}
REQUIRED = [
    "qc_organisation_registration", "qc_tin", "qc_laboratory_registration",
    "qc_accreditation_certificate", "qc_accreditation_scope",
]


def pdf():
    return SimpleUploadedFile("evidence.pdf", b"%PDF-1.4 evidence", content_type="application/pdf")


class MediaTestCase(APITestCase):
    def setUp(self):
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        override = override_settings(MEDIA_ROOT=media.name)
        override.enable()
        self.addCleanup(override.disable)


class QualityOrganisationOnboardingTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.owner = User.objects.create_user("owner@lab.test", email_verified_at=timezone.now())
        self.staff = User.objects.create_user("operator@beldium.test", is_staff=True)
        self.org = Organisation.objects.create(name="Draft Lab", organisation_type="laboratory")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role="owner")
        self.app = ComplianceApplication.objects.create(organisation=self.org, created_by=self.owner)
        self.client.force_authenticate(self.owner)

    def url(self, action, *args):
        return reverse("compliance-application-" + action, args=[self.app.id, *args])

    def save(self, section, data=None):
        return self.client.patch(
            self.url("quality-section", section), {"data": SECTIONS[section] if data is None else data}, format="json",
        )

    def fill(self):
        for section in SECTIONS:
            response = self.save(section)
            self.assertEqual(response.status_code, 200, (section, response.data))
        for document_type in REQUIRED:
            response = self.client.post(self.url("documents"), {"document_type": document_type, "file": pdf()}, format="multipart")
            self.assertEqual(response.status_code, 201, response.data)

    def test_sections_save_and_write_through_to_the_organisation(self):
        response = self.save("organisation")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["quality_profile"]["organisation"]["trading_name"], "Jos Assay")
        self.assertEqual(response.data["organisation_profile"]["name"], "Jos Assay Ltd")
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Jos Assay Ltd")
        self.assertEqual(self.org.organisation_type, "laboratory")

    def test_section_validation_and_unknown_section(self):
        rejected = self.save("services", {"capabilities": [], "minerals": ["Gold"]})
        self.assertEqual(rejected.status_code, 400)
        unsigned = self.save("declaration", {**SECTIONS["declaration"], "consent_to_verification": False})
        self.assertEqual(unsigned.status_code, 400)
        self.assertEqual(self.save("nonsense", {}).status_code, 404)

    def test_checklist_switches_to_quality_documents(self):
        mining_only = self.client.post(self.url("documents"), {"document_type": "qc_tin", "file": pdf()}, format="multipart")
        self.assertEqual(mining_only.data["error"]["code"], "unknown_document_type")
        self.save("organisation")
        requirements = self.client.get(self.url("document-requirements")).data
        self.assertEqual(len(requirements), 11)
        self.assertEqual([r["document_type"] for r in requirements if r["required"]], REQUIRED)

    def test_complete_application_reaches_the_quality_desk_and_decision_returns(self):
        self.fill()
        progress = self.client.get(reverse("compliance-application-detail", args=[self.app.id])).data["progress"]
        self.assertEqual(progress["percent"], 100, progress)
        self.assertEqual(self.client.post(self.url("submit")).status_code, 200)

        mirror = QualityApplication.objects.get(compliance_application=self.app)
        self.assertEqual(mirror.organisation, self.org)
        self.assertEqual(mirror.organisation_data["legal_name"], "Jos Assay Ltd")
        self.assertEqual(mirror.laboratory_data["certificate_no"], "TL-0042")
        self.assertEqual([row["method"] for row in mirror.laboratory_data["scope"]], ["ICP-OES", "XRF"])
        self.assertEqual(len(mirror.documents), 5)

        # The applicant, a laboratory member, sees their own mirrored record.
        listed = self.client.get(reverse("quality-application-list")).data["results"]
        self.assertEqual([row["id"] for row in listed], [str(mirror.id)])

        self.client.force_authenticate(self.staff)
        doc_id = mirror.documents[0]["id"]
        marked = self.client.patch(
            reverse("quality-application-document-status", args=[mirror.id, doc_id]), {"status": "verified"}, format="json",
        )
        self.assertEqual(marked.status_code, 200, marked.data)
        self.assertEqual(self.app.documents.get(pk=doc_id).status, "verified")
        download = self.client.get(reverse("quality-application-document-download", args=[mirror.id, doc_id]))
        self.assertEqual(download.status_code, 200)

        decided = self.client.post(
            reverse("quality-application-decide", args=[mirror.id]), {"status": "approved", "note": "Accredited."}, format="json",
        )
        self.assertEqual(decided.status_code, 200, decided.data)
        self.app.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(self.app.status, "verified")
        self.assertEqual(self.org.verification_status, "verified")

    def test_information_request_and_resubmission_round_trip(self):
        self.fill()
        self.client.post(self.url("submit"))
        mirror = QualityApplication.objects.get(compliance_application=self.app)
        self.client.force_authenticate(self.staff)
        self.client.post(reverse("quality-application-decide", args=[mirror.id]), {"status": "info_requested"}, format="json")
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "action_required")

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.save("services").status_code, 200)
        self.assertEqual(self.client.post(self.url("submit")).status_code, 200)
        mirror.refresh_from_db()
        self.assertEqual(mirror.status, "submitted")
        self.assertEqual(QualityApplication.objects.count(), 1)


class QualityProfessionalApplicationTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.applicant = User.objects.create_user("inspector@field.test", email_verified_at=timezone.now())
        self.other = User.objects.create_user("other@field.test", email_verified_at=timezone.now())
        self.staff = User.objects.create_user("operator@beldium.test", is_staff=True)
        self.org = Organisation.objects.create(name="Sahel Inspection", organisation_type="inspection_body", verification_status="verified")
        self.url = reverse("quality-professional-application-list")
        self.client.force_authenticate(self.applicant)

    def payload(self, **files):
        data = {
            "organisation": str(self.org.id),
            "role": "officer_inspector",
            "personal": {"full_legal_name": "Amina Bello", "date_of_birth": "1990-04-02", "national_id": "12345678901", "job_title": "Inspector", "base_city": "Kaduna"},
            "qualifications": [{"qualification": "BSc Geology", "institution": "ABU", "year": "2012"}],
            "certifications": [],
            "capability": {"capabilities": ["Sampling"], "minerals": ["Gold"]},
            "experience": {"years_experience": 8, "previous_employer": "", "summary": "Field sampling."},
            "declaration": {"information_true": True, "consent_to_verification": True, "understands_verification": True, "signature": "Amina Bello"},
        }
        files = files or {"professional_qualifications": pdf(), "inspection_credentials": pdf()}
        return {"data": json.dumps(data), **files}

    def test_submit_read_and_decide(self):
        created = self.client.post(self.url, self.payload(), format="multipart")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(created.data["reference"].startswith("BLD-QA-PRO-"))
        self.assertEqual(len(created.data["documents"]), 2)
        application = QualityProfessionalApplication.objects.get()
        self.assertEqual(application.personal["date_of_birth"], "1990-04-02")

        duplicate = self.client.post(self.url, self.payload(), format="multipart")
        self.assertEqual(duplicate.status_code, 409)

        detail = reverse("quality-professional-application-detail", args=[application.id])
        decide = reverse("quality-professional-application-decide", args=[application.id])
        self.assertEqual(self.client.post(decide, {"status": "approved"}, format="json").status_code, 403)

        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).data["results"], [])
        self.assertEqual(self.client.get(detail).status_code, 404)

        self.client.force_authenticate(self.staff)
        self.assertEqual(len(self.client.get(self.url).data["results"]), 1)
        document = application.documents.first()
        download = self.client.get(reverse("quality-professional-application-document-download", args=[application.id, document.id]))
        self.assertEqual(download.status_code, 200)
        decided = self.client.post(decide, {"status": "approved", "note": "Credentials confirmed."}, format="json")
        self.assertEqual(decided.status_code, 200, decided.data)
        self.assertEqual(decided.data["status"], "approved")

    def test_required_document_and_declaration_are_enforced(self):
        missing = self.client.post(self.url, self.payload(professional_qualifications=pdf()), format="multipart")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(QualityProfessionalApplication.objects.count(), 0)
