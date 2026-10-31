import json
from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from compliance.logistics_documents import LOGISTICS_DOCUMENT_DOMAINS, LOGISTICS_DOCUMENTS, LOGISTICS_REQUIRED_DOCUMENT_TYPES
from compliance.models import ComplianceApplication, ComplianceDocument
from logistics import services
from logistics.models import DomainReview, LogisticsApplication, LogisticsCompany
from organisations.models import Organisation, OrganisationMembership

User = get_user_model()


def pdf():
    return SimpleUploadedFile("evidence.pdf", b"%PDF-1.4 evidence", content_type="application/pdf")


class LogisticsDocumentCase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@haulage.test", email_verified_at=timezone.now())
        self.outsider = User.objects.create_user("outsider@haulage.test", email_verified_at=timezone.now())
        self.org = Organisation.objects.create(name="Haulage Org", organisation_type="compliance_partner")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role="owner")

    def application(self, **overrides):
        return ComplianceApplication.objects.create(organisation=self.org, created_by=self.owner, sector="logistics", **overrides)

    def url(self, application, name):
        return reverse(f"compliance-application-{name}", args=[application.id])


class ComplianceChecklistTests(LogisticsDocumentCase):
    def test_checklist_lists_all_fifteen_and_marks_eight_required(self):
        self.client.force_authenticate(self.owner)
        rows = self.client.get(self.url(self.application(), "document-requirements")).data
        self.assertEqual([row["document_type"] for row in rows], [row[0] for row in LOGISTICS_DOCUMENTS])
        self.assertEqual({row["document_type"] for row in rows if row["required"]}, LOGISTICS_REQUIRED_DOCUMENT_TYPES)
        self.assertEqual(len(LOGISTICS_REQUIRED_DOCUMENT_TYPES), 8)
        lease = next(row for row in rows if row["document_type"] == "logistics_lease_declaration")
        self.assertEqual(lease["applies_when"], "If any vehicle is leased or contracted")

    def test_other_sectors_keep_their_own_checklist(self):
        self.client.force_authenticate(self.owner)
        application = ComplianceApplication.objects.create(organisation=self.org, created_by=self.owner, sector="mining")
        types = {row["document_type"] for row in self.client.get(self.url(application, "document-requirements")).data}
        self.assertIn("mining_sector_registrations", types)
        self.assertNotIn("logistics_vehicle_schedule", types)

    def test_old_files_stop_being_asked_for_unless_the_desk_needs_an_answer(self):
        application = self.application()
        ComplianceDocument.objects.create(application=application, document_type="company_profile", title="Company Profile", file=pdf(), status="verified")
        ComplianceDocument.objects.create(application=application, document_type="code_of_conduct", title="Code of Conduct", file=pdf(), status="rejected")
        self.client.force_authenticate(self.owner)
        types = {row["document_type"] for row in self.client.get(self.url(application, "document-requirements")).data}
        self.assertNotIn("company_profile", types)
        self.assertIn("code_of_conduct", types)

    def test_progress_needs_only_the_required_documents(self):
        application = self.application()
        for document_type in LOGISTICS_REQUIRED_DOCUMENT_TYPES:
            ComplianceDocument.objects.create(application=application, document_type=document_type, title=document_type, file=pdf(), status="submitted")
        self.client.force_authenticate(self.owner)
        progress = self.client.get(reverse("compliance-application-detail", args=[application.id])).data["progress"]
        self.assertTrue(progress["sections"]["documents"])
        self.assertEqual(progress["documents"]["required"], 8)

    def test_old_document_type_is_refused_for_a_new_upload(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url(self.application(), "documents"), {"document_type": "mining_sector_registrations", "file": pdf()}, format="multipart")
        self.assertEqual(response.data["error"]["code"], "unknown_document_type")

    def test_verified_account_can_add_a_missing_document_and_stays_verified(self):
        application = self.application(status="verified")
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url(application, "documents"), {"document_type": "logistics_vehicle_schedule", "file": pdf()}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        application.refresh_from_db()
        self.assertEqual(application.status, "verified")

    def test_verified_account_cannot_replace_verified_evidence_or_old_documents(self):
        application = self.application(status="verified")
        ComplianceDocument.objects.create(application=application, document_type="logistics_tax_documentation", title="Tax", file=pdf(), status="verified")
        self.client.force_authenticate(self.owner)
        for document_type in ("logistics_tax_documentation", "company_profile"):
            response = self.client.post(self.url(application, "documents"), {"document_type": document_type, "file": pdf()}, format="multipart")
            self.assertEqual(response.data["error"]["code"], "application_locked")

    def test_someone_outside_the_organisation_cannot_add_a_document(self):
        application = self.application(status="verified")
        self.client.force_authenticate(self.outsider)
        response = self.client.post(self.url(application, "documents"), {"document_type": "logistics_vehicle_schedule", "file": pdf()}, format="multipart")
        self.assertIn(response.status_code, {403, 404})
        self.assertFalse(application.documents.exists())


class CarryOverMigrationTests(LogisticsDocumentCase):
    def test_only_logistics_documents_are_carried_over_and_it_reverses(self):
        migration = import_module("compliance.migrations.0006_carry_logistics_documents_to_new_checklist")
        logistics = ComplianceDocument.objects.create(application=self.application(), document_type="tin_evidence", title="TIN Evidence", file=pdf(), status="verified")
        mining_org = Organisation.objects.create(name="Mining Org", organisation_type="compliance_partner")
        mining_application = ComplianceApplication.objects.create(organisation=mining_org, created_by=self.owner, sector="mining")
        mining = ComplianceDocument.objects.create(application=mining_application, document_type="tin_evidence", title="TIN Evidence", file=pdf())

        migration.carry_over(apps, None)
        logistics.refresh_from_db()
        mining.refresh_from_db()
        self.assertEqual((logistics.document_type, logistics.status), ("logistics_tax_documentation", "verified"))
        self.assertEqual(mining.document_type, "tin_evidence")

        migration.carry_back(apps, None)
        logistics.refresh_from_db()
        self.assertEqual((logistics.document_type, logistics.title), ("tin_evidence", "TIN Evidence"))


class LogisticsCompanyChecklistTests(LogisticsDocumentCase):
    def setUp(self):
        super().setUp()
        self.company = LogisticsCompany.objects.create(organisation=self.org, contact_email="ops@haulage.test", services=["general freight"])

    def company_application(self, **overrides):
        application = LogisticsApplication.objects.create(company=self.company, created_by=self.owner, **overrides)
        services.initialise(application)
        return application

    def upload(self, application, document_type):
        return self.client.post(
            reverse("logistics-application-documents", args=[application.id]),
            {"domain": LOGISTICS_DOCUMENT_DOMAINS[document_type], "document_type": document_type, "title": document_type, "file": pdf()},
            format="multipart",
        )

    def test_progress_names_the_required_types_still_missing(self):
        application = self.company_application()
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.upload(application, "logistics_vehicle_schedule").status_code, 201)
        progress = services.progress(application)
        self.assertEqual(len(progress["missing_document_types"]), 7)
        self.assertNotIn("logistics_vehicle_schedule", progress["missing_document_types"])
        self.assertFalse(progress["checks"]["documents"])

    def test_approved_company_can_add_a_checklist_document_without_reopening(self):
        application = self.company_application(status="approved")
        DomainReview.objects.filter(application=application, key="fleet").update(status="passed", score=90)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.upload(application, "logistics_vehicle_schedule").status_code, 201)
        application.refresh_from_db()
        self.assertEqual(application.status, "approved")
        self.assertEqual(application.sections.get(key="fleet").status, "passed")

    def test_company_under_review_can_add_a_checklist_document(self):
        application = self.company_application(status="under_review")
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.upload(application, "logistics_vehicle_schedule").status_code, 201)
        application.refresh_from_db()
        self.assertEqual(application.status, "under_review")

    def test_approved_company_still_cannot_add_something_off_the_checklist(self):
        application = self.company_application(status="approved")
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-application-documents", args=[application.id]),
            {"domain": "fleet", "document_type": "something_else", "title": "Other", "file": pdf()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 409)

    def test_someone_outside_the_company_cannot_upload(self):
        application = self.company_application(status="approved")
        self.client.force_authenticate(self.outsider)
        self.assertIn(self.upload(application, "logistics_vehicle_schedule").status_code, {403, 404})
        self.assertFalse(application.documents.exists())


class LaboratoryAnalystDocumentTests(LogisticsDocumentCase):
    def payload(self, role, **files):
        data = {
            "organisation": str(self.org.id),
            "role": role,
            "personal": {"full_legal_name": "Amina Bello", "date_of_birth": "1990-04-02", "national_id": "12345678901", "job_title": "Analyst", "base_city": "Jos"},
            "capability": {"capabilities": ["XRF"], "minerals": ["Gold"]},
            "experience": {"years_experience": 8, "previous_employer": "", "summary": "Bench work."},
            "declaration": {"information_true": True, "consent_to_verification": True, "understands_verification": True, "signature": "Amina Bello"},
        }
        return {"data": json.dumps(data), **files}

    def post(self, *args, **kwargs):
        self.client.force_authenticate(self.owner)
        return self.client.post(reverse("quality-professional-application-list"), self.payload(*args, **kwargs), format="multipart")

    def test_analyst_needs_only_qualifications_and_can_add_method_evidence(self):
        response = self.post("laboratory_analyst", professional_qualifications=pdf(), testing_method_evidence=pdf())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual({doc["document_type"] for doc in response.data["documents"]}, {"professional_qualifications", "testing_method_evidence"})
        self.assertEqual(response.data["role"], "laboratory_analyst")

    def test_analyst_without_qualifications_is_refused(self):
        self.assertEqual(self.post("laboratory_analyst", testing_method_evidence=pdf()).status_code, 400)

    def test_officer_still_needs_inspection_credentials(self):
        self.assertEqual(self.post("officer_inspector", professional_qualifications=pdf()).status_code, 400)
