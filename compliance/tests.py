from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import ApplicationStatus, ComplianceApplication, ComplianceDocument, REQUIRED_DOCUMENTS
from organisations.models import Organisation


@override_settings(MEDIA_ROOT="/tmp/beldium-compliance-tests")
class ComplianceApplicationLifecycleTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!", email_verified_at=timezone.now())
        self.admin = User.objects.create_superuser("admin@example.com", "SafePassword-2026!")
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("organisation-list"), {"name": "Assurance Ltd", "organisation_type": "compliance_partner", "registration_number": "RC-90001"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.organisation = Organisation.objects.get(id=response.data["id"])

    def test_full_onboarding_submission_and_dashboard(self):
        created = self.client.post(reverse("compliance-application-list"), {"organisation": self.organisation.id})
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        application_id = created.data["id"]
        sections = {
            "organisation": {
                "name": "Assurance Ltd", "organisation_type": "compliance_partner",
                "registration_number": "RC-90001", "tax_identifier": "TIN-90001",
                "year_established": 2020, "website": "https://assurance.example.com",
                "registered_address": "Abuja", "operating_address": "Abuja",
                "country": "Nigeria", "state": "FCT", "lga": "AMAC",
            },
            "representative": {"full_name": "Ada Owner", "position": "Director", "official_email": "owner@example.com", "official_phone": "+2348012345678", "authorised": True},
            "services": {"selected_services": ["mine_inspection"], "geographic_coverage": "nationwide"},
            "professional-capability": {"years_mining_experience": 8, "mining_engineers": 2},
            "inspection-capability": {"conducts_physical_inspections": True, "gps": True},
            "conflict-declaration": {"owns_assets": False, "serves_mining_companies": True, "trades_minerals": False, "relationships": "None", "agreed": True},
            "declaration": {"accuracy_confirmed": True, "documents_genuine": True, "compliance_agreed": True, "disclose_changes": True, "authorised": True, "confirmed": True},
        }
        for name, data in sections.items():
            route = f"compliance-application-{name}-section"
            args = [application_id]
            response = self.client.patch(reverse(route, args=args), {"data": data}, format="json")
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        person = self.client.post(reverse("compliance-application-personnel", args=[application_id]), {"full_name": "Emeka Inspector", "role": "Inspector", "years_experience": 5})
        self.assertEqual(person.status_code, status.HTTP_201_CREATED)
        for document_type, title in REQUIRED_DOCUMENTS:
            upload = SimpleUploadedFile(f"{document_type}.pdf", b"%PDF-1.4 test", content_type="application/pdf")
            document = self.client.post(reverse("compliance-application-documents", args=[application_id]), {"document_type": document_type, "title": title, "file": upload}, format="multipart")
            self.assertEqual(document.status_code, status.HTTP_201_CREATED, document.data)

        submitted = self.client.post(reverse("compliance-application-submit", args=[application_id]))
        self.assertEqual(submitted.status_code, status.HTTP_200_OK, submitted.data)
        self.assertEqual(submitted.data["status"], ApplicationStatus.UNDER_REVIEW)
        dashboard = self.client.get(reverse("dashboard-list"))
        self.assertEqual(dashboard.data["applications"][0]["progress"]["percent"], 100)

    def test_staff_requests_document_and_owner_uploads_it(self):
        application = ComplianceApplication.objects.create(organisation=self.organisation, status=ApplicationStatus.UNDER_REVIEW)
        self.client.force_authenticate(self.admin)
        requested = self.client.post(reverse("compliance-application-request-document", args=[application.id]), {"document_type": "insurance", "title": "Insurance", "request_message": "Upload current cover."})
        self.assertEqual(requested.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ComplianceDocument.objects.get().status, ComplianceDocument.Status.REQUESTED)
        self.client.force_authenticate(self.owner)
        upload = SimpleUploadedFile("insurance.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        submitted = self.client.post(reverse("compliance-application-documents", args=[application.id]), {"document_type": "insurance", "title": "Insurance", "file": upload}, format="multipart")
        self.assertEqual(submitted.status_code, status.HTTP_200_OK, submitted.data)
        self.assertEqual(ComplianceDocument.objects.get().status, ComplianceDocument.Status.SUBMITTED)

    def test_incomplete_section_and_submission_are_rejected(self):
        application = ComplianceApplication.objects.create(organisation=self.organisation)
        section = self.client.patch(reverse("compliance-application-declaration-section", args=[application.id]), {"data": {"confirmed": True}}, format="json")
        self.assertEqual(section.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(section.data["error"]["code"], "validation_error")
        submitted = self.client.post(reverse("compliance-application-submit", args=[application.id]))
        self.assertEqual(submitted.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(submitted.data["error"]["code"], "application_incomplete")
