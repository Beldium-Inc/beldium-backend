from datetime import timedelta
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from export import services
from export.models import (
    Buyer,
    Domain,
    DomainReview,
    ExportAccessGrant,
    ExportApplication,
    ExportCondition,
    ExportDocument,
    ExportReport,
    Exporter,
    InformationRequest,
    Notification,
    Product,
    Shipment,
)
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType


def make_user(email, **extra):
    return User.objects.create_user(
        email=email,
        password="Str0ng-Passw0rd!",
        email_verified_at=timezone.now(),
        **extra,
    )


def make_org(name):
    return Organisation.objects.create(
        name=name,
        organisation_type=OrganisationType.MINING_COMPANY,
        registration_number=name.upper().replace(" ", "-"),
        verification_status="verified",
    )


class ExportTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@export.test")
        self.reviewer = make_user("reviewer@desk.test")
        self.outsider = make_user("outsider@other.test")

        self.org = make_org("Beldium Export House")
        self.other_org = make_org("Other Exporter")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role=MembershipRole.OWNER)
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)

        self.exporter = Exporter.objects.create(
            organisation=self.org,
            contact_name="Ada Bello",
            contact_email="exports@example.test",
            contact_phone="+2348000000000",
            destinations=["Ghana", "UAE"],
            product_categories=["lithium ore", "tin concentrate"],
        )
        ExportAccessGrant.objects.create(exporter=self.exporter, user=self.reviewer, role="reviewer")

    def make_application(self, **overrides):
        application = ExportApplication.objects.create(exporter=self.exporter, created_by=self.owner, **overrides)
        services.initialise(application)
        return application

    def make_product(self, **overrides):
        defaults = {
            "exporter": self.exporter,
            "name": "Lithium ore",
            "hs_code": "2607.00",
            "origin_state": "Nasarawa",
            "annual_capacity": "5000.00",
        }
        return Product.objects.create(**{**defaults, **overrides})

    def make_buyer(self, **overrides):
        defaults = {
            "exporter": self.exporter,
            "name": "Accra Metals",
            "country": "Ghana",
            "screening_status": "cleared",
        }
        return Buyer.objects.create(**{**defaults, **overrides})

    def make_shipment(self, **overrides):
        product = overrides.pop("product", None) or self.make_product()
        buyer = overrides.pop("buyer", None) or self.make_buyer()
        defaults = {
            "exporter": self.exporter,
            "product": product,
            "buyer": buyer,
            "destination_country": buyer.country,
            "port_of_loading": "Lagos",
            "port_of_discharge": "Tema",
            "quantity": "25.00",
            "estimated_value": "120000.00",
            "expected_ship_date": timezone.localdate() + timedelta(days=30),
        }
        return Shipment.objects.create(**{**defaults, **overrides})

    def add_document(self, application, domain, **overrides):
        defaults = {
            "application": application,
            "domain": domain,
            "document_type": f"{domain}_evidence",
            "title": f"{domain.title()} evidence",
            "expires_on": timezone.localdate() + timedelta(days=120),
            "status": "verified",
            "file": SimpleUploadedFile(f"{domain}.pdf", b"%PDF-1.4", content_type="application/pdf"),
            "original_name": f"{domain}.pdf",
            "uploaded_by": self.owner,
        }
        return ExportDocument.objects.create(**{**defaults, **overrides})

    def complete_application(self, application):
        shipment = self.make_shipment()
        DomainReview.objects.filter(application=application, applicable=True).update(
            data={"declared": True},
            status="passed",
            score=90,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )
        for key in application.sections.filter(applicable=True).values_list("key", flat=True):
            self.add_document(
                application,
                key,
                shipment=shipment if key == Domain.SHIPMENT else None,
            )
        return application


class ExportAudienceTests(ExportTestCase):
    def test_capabilities_are_resolved_from_membership_and_grants(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("export-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["exporters"][0]["can_edit"])
        self.assertFalse(response.data["exporters"][0]["can_review"])

        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("export-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["exporters"][0]["can_review"])
        self.assertFalse(response.data["exporters"][0]["can_edit"])

    def test_exporter_user_does_not_see_other_exporters(self):
        Exporter.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="other@example.test",
            contact_phone="+2348111111111",
            destinations=["Benin"],
            product_categories=["gold"],
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("export-exporter-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.exporter.id))

    def test_reviewer_grant_is_read_only_for_exporter_records(self):
        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("export-exporter-list"))
        self.assertEqual(response.status_code, 200)

        response = self.client.patch(
            reverse("export-exporter-detail", args=[self.exporter.id]),
            {"contact_name": "Changed"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_outsider_cannot_create_exporter_for_unmanaged_organisation(self):
        unmanaged_org = make_org("Unmanaged Export Org")
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            reverse("export-exporter-list"),
            {
                "organisation": str(unmanaged_org.id),
                "contact_name": "Intruder",
                "contact_email": "intruder@example.test",
                "contact_phone": "+2348000000001",
                "destinations": ["Ghana"],
                "product_categories": ["tin"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_access_grants_are_admin_only_and_can_be_updated(self):
        staff = make_user("staff@desk.test", is_staff=True)
        new_reviewer = make_user("new-reviewer@desk.test")

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("export-access-grant-list"),
            {"exporter": str(self.exporter.id), "user": str(new_reviewer.id), "role": "reviewer"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(staff)
        response = self.client.post(
            reverse("export-access-grant-list"),
            {"exporter": str(self.exporter.id), "user": str(new_reviewer.id), "role": "reviewer"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        response = self.client.patch(
            reverse("export-access-grant-detail", args=[response.data["id"]]),
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])

    def test_exporter_serializer_rejects_duplicate_destinations_and_categories(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            reverse("export-exporter-detail", args=[self.exporter.id]),
            {"destinations": ["Ghana", "ghana"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("destinations", response.data["error"]["details"])

        response = self.client.patch(
            reverse("export-exporter-detail", args=[self.exporter.id]),
            {"product_categories": ["Tin", "tin"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("product_categories", response.data["error"]["details"])


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class ExportWorkflowTests(ExportTestCase):
    def test_application_creation_lays_down_export_domains(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("export-application-list"),
            {"exporter": str(self.exporter.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        application = ExportApplication.objects.get(id=response.data["id"])
        self.assertEqual(application.sections.count(), len(Domain.choices))

    def test_submission_requires_records_sections_and_documents(self):
        application = self.make_application()
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("export-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "application_incomplete")

        self.make_shipment()
        DomainReview.objects.filter(application=application).update(data={"declared": True})
        response = self.client.post(reverse("export-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertIn("exporter", response.data["error"]["details"]["missing_document_domains"])

    def test_product_buyer_and_shipment_crud_use_owner_permissions(self):
        self.client.force_authenticate(self.owner)
        product_response = self.client.post(
            reverse("export-product-list"),
            {
                "exporter": str(self.exporter.id),
                "name": "Tin concentrate",
                "hs_code": "2609.00",
                "origin_state": "Plateau",
                "annual_capacity": "1500.00",
            },
            format="json",
        )
        self.assertEqual(product_response.status_code, 201)

        buyer_response = self.client.post(
            reverse("export-buyer-list"),
            {
                "exporter": str(self.exporter.id),
                "name": "Dubai Smelters",
                "country": "UAE",
                "screening_status": "cleared",
            },
            format="json",
        )
        self.assertEqual(buyer_response.status_code, 201)

        shipment_response = self.client.post(
            reverse("export-shipment-list"),
            {
                "exporter": str(self.exporter.id),
                "product": product_response.data["id"],
                "buyer": buyer_response.data["id"],
                "destination_country": "UAE",
                "port_of_loading": "Lagos",
                "port_of_discharge": "Jebel Ali",
                "quantity": "10.00",
                "estimated_value": "50000.00",
                "expected_ship_date": str(timezone.localdate() + timedelta(days=20)),
            },
            format="json",
        )
        self.assertEqual(shipment_response.status_code, 201)
        self.assertEqual(Shipment.objects.count(), 1)

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-product-list"),
            {
                "exporter": str(self.exporter.id),
                "name": "Blocked product",
                "hs_code": "2610.00",
                "annual_capacity": "1.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_list_filters_and_search_work_for_export_records(self):
        active_product = self.make_product(name="Lithium ore", hs_code="2607.00", controlled=True)
        Product.objects.create(
            exporter=self.exporter,
            name="Tin concentrate",
            hs_code="2609.00",
            is_active=False,
        )
        buyer = self.make_buyer(name="Accra Metals", country="Ghana")
        self.make_shipment(product=active_product, buyer=buyer, status="ready")

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("export-product-list"), {"controlled": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["name"], "Lithium ore")

        response = self.client.get(reverse("export-product-list"), {"search": "Tin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["hs_code"], "2609.00")

        response = self.client.get(reverse("export-shipment-list"), {"status": "ready"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_shipment_rejects_product_from_another_exporter(self):
        other_exporter = Exporter.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="other@example.test",
            contact_phone="+2348111111111",
            destinations=["Benin"],
            product_categories=["gold"],
        )
        other_product = Product.objects.create(exporter=other_exporter, name="Gold", hs_code="7108.00")
        buyer = self.make_buyer()

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("export-shipment-list"),
            {
                "exporter": str(self.exporter.id),
                "product": str(other_product.id),
                "buyer": str(buyer.id),
                "destination_country": "Ghana",
                "port_of_loading": "Lagos",
                "port_of_discharge": "Tema",
                "quantity": "10.00",
                "expected_ship_date": str(timezone.localdate() + timedelta(days=20)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("product", response.data["error"]["details"])

    def test_owner_can_update_section_and_upload_document(self):
        application = self.make_application()
        shipment = self.make_shipment()

        self.client.force_authenticate(self.owner)
        section_response = self.client.patch(
            reverse("export-application-section", args=[application.id, Domain.SHIPMENT]),
            {"data": {"incoterm": "FOB", "port": "Lagos"}},
            format="json",
        )
        self.assertEqual(section_response.status_code, 200)
        self.assertEqual(section_response.data["data"]["incoterm"], "FOB")

        upload = SimpleUploadedFile("shipment.pdf", b"%PDF-1.4", content_type="application/pdf")
        document_response = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.SHIPMENT,
                "document_type": "shipment_plan",
                "title": "Shipment plan",
                "expires_on": str(timezone.localdate() + timedelta(days=90)),
                "shipment": str(shipment.id),
                "file": upload,
            },
            format="multipart",
        )
        self.assertEqual(document_response.status_code, 201)
        self.assertEqual(document_response.data["domain"], Domain.SHIPMENT)
        self.assertEqual(ExportDocument.objects.count(), 1)

    def test_document_version_replacement_retires_previous_current_document(self):
        application = self.make_application()

        self.client.force_authenticate(self.owner)
        first = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.EXPORTER,
                "document_type": "export_license",
                "title": "Export licence",
                "file": SimpleUploadedFile("license-v1.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(first.status_code, 201)

        second = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.EXPORTER,
                "document_type": "export_license",
                "title": "Export licence renewal",
                "file": SimpleUploadedFile("license-v2.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data["version"], 2)
        self.assertFalse(ExportDocument.objects.get(id=first.data["id"]).is_current)
        self.assertTrue(ExportDocument.objects.get(id=second.data["id"]).is_current)

    def test_document_version_rejects_identity_changes(self):
        application = self.make_application()
        self.add_document(
            application,
            Domain.EXPORTER,
            document_type="shared_record",
            status="pending",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.CUSTOMS,
                "document_type": "shared_record",
                "title": "Changed identity",
                "file": SimpleUploadedFile("changed.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "document_identity_changed")

    def test_document_upload_validates_file_type_and_cross_exporter_shipment(self):
        application = self.make_application()
        other_exporter = Exporter.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="other@example.test",
            contact_phone="+2348111111111",
            destinations=["Benin"],
            product_categories=["gold"],
        )
        other_product = Product.objects.create(exporter=other_exporter, name="Gold", hs_code="7108.00")
        other_buyer = Buyer.objects.create(exporter=other_exporter, name="Cotonou Buyer", country="Benin")
        other_shipment = Shipment.objects.create(
            exporter=other_exporter,
            product=other_product,
            buyer=other_buyer,
            destination_country="Benin",
            port_of_loading="Lagos",
            port_of_discharge="Cotonou",
            quantity="5.00",
            expected_ship_date=timezone.localdate() + timedelta(days=15),
        )

        self.client.force_authenticate(self.owner)
        bad_file = SimpleUploadedFile("evidence.txt", b"plain", content_type="text/plain")
        response = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.SHIPMENT,
                "document_type": "shipment_plan",
                "title": "Shipment plan",
                "shipment": str(other_shipment.id),
                "file": bad_file,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data["error"]["details"])

        good_file = SimpleUploadedFile("evidence.pdf", b"%PDF-1.4", content_type="application/pdf")
        response = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.SHIPMENT,
                "document_type": "shipment_plan",
                "title": "Shipment plan",
                "shipment": str(other_shipment.id),
                "file": good_file,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("shipment", response.data["error"]["details"])

    def test_document_upload_rejects_invalid_issue_and_expiry_dates(self):
        application = self.make_application()
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("export-application-documents", args=[application.id]),
            {
                "domain": Domain.EXPORTER,
                "document_type": "dated_license",
                "title": "Dated licence",
                "issued_on": str(timezone.localdate()),
                "expires_on": str(timezone.localdate() - timedelta(days=1)),
                "file": SimpleUploadedFile("dated.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("expires_on", response.data["error"]["details"])

    def test_expiring_documents_endpoint_returns_only_due_documents(self):
        application = self.make_application()
        due = self.add_document(application, Domain.EXPORTER, expires_on=timezone.localdate() + timedelta(days=5))
        self.add_document(application, Domain.PRODUCT, expires_on=timezone.localdate() + timedelta(days=90))

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("export-document-expiring"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], str(due.id))

    def test_document_review_and_download_are_restricted_to_export_audience(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        document = self.add_document(application, Domain.EXPORTER, status="pending")

        self.client.force_authenticate(self.reviewer)
        review_response = self.client.post(
            reverse("export-document-review", args=[document.id]),
            {"status": "verified", "notes": "Looks good"},
            format="json",
        )
        self.assertEqual(review_response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.status, "verified")

        download_response = self.client.get(reverse("export-document-download", args=[document.id]))
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response["X-Content-Type-Options"], "nosniff")

        self.client.force_authenticate(self.outsider)
        response = self.client.get(reverse("export-document-detail", args=[document.id]))
        self.assertEqual(response.status_code, 404)

    def test_reviewer_cannot_verify_expired_evidence(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        document = self.add_document(
            application,
            Domain.EXPORTER,
            status="pending",
            expires_on=timezone.localdate() - timedelta(days=1),
        )

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-document-review", args=[document.id]),
            {"status": "verified", "notes": "Expired"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_reviewer_assignment_start_review_and_domain_review_flow(self):
        application = self.make_application(status="submitted")
        self.add_document(application, Domain.EXPORTER, status="verified")
        application.sections.filter(key=Domain.EXPORTER).update(data={"profile": True})

        self.client.force_authenticate(self.reviewer)
        assign_response = self.client.post(
            reverse("export-application-assign-reviewer", args=[application.id]),
            {"reviewer": str(self.reviewer.id)},
            format="json",
        )
        self.assertEqual(assign_response.status_code, 200)

        start_response = self.client.post(reverse("export-application-start-review", args=[application.id]))
        self.assertEqual(start_response.status_code, 200)

        review_response = self.client.post(
            reverse("export-application-review-section", args=[application.id, Domain.EXPORTER]),
            {"status": "passed", "score": 95, "notes": "Accepted", "applicable": True},
            format="json",
        )
        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response.data["status"], "passed")

    def test_unassigned_reviewer_cannot_start_review(self):
        application = self.make_application(status="submitted")
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("export-application-start-review", args=[application.id]))
        self.assertEqual(response.status_code, 403)

    def test_complete_application_can_be_submitted_and_approved(self):
        application = self.complete_application(self.make_application())

        self.client.force_authenticate(self.owner)
        submit_response = self.client.post(reverse("export-application-submit", args=[application.id]))
        self.assertEqual(submit_response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.status, "submitted")

        application.reviewer = self.reviewer
        application.status = "under_review"
        application.save(update_fields=["reviewer", "status"])

        self.client.force_authenticate(self.reviewer)
        decision_response = self.client.post(
            reverse("export-application-decide", args=[application.id]),
            {"status": "approved", "rationale": "All controls passed"},
            format="json",
        )
        self.assertEqual(decision_response.status_code, 200)
        self.assertEqual(decision_response.data["status"], "approved")

    def test_rejected_decision_does_not_require_complete_application(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-application-decide", args=[application.id]),
            {"status": "rejected", "rationale": "Insufficient export controls"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "rejected")

    def test_conditional_approval_with_inline_conditions(self):
        application = self.complete_application(self.make_application(status="under_review", reviewer=self.reviewer))
        application.sections.filter(key=Domain.FINANCE).update(status="attention")

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-application-decide", args=[application.id]),
            {
                "status": "conditionally_approved",
                "rationale": "Finance reconciliation pending",
                "conditions": [
                    {
                        "title": "Submit proceeds repatriation plan",
                        "description": "Finance desk requires the plan before final approval.",
                        "due_date": str(timezone.localdate() + timedelta(days=14)),
                        "domain": Domain.FINANCE,
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "conditionally_approved")
        self.assertEqual(ExportCondition.objects.filter(application=application).count(), 1)

    def test_conditional_approval_requires_inline_or_existing_condition(self):
        application = self.complete_application(self.make_application(status="under_review", reviewer=self.reviewer))

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-application-decide", args=[application.id]),
            {"status": "conditionally_approved", "rationale": "Needs follow-up"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_reviewer_can_request_information_and_owner_can_respond(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        document = self.add_document(application, Domain.EXPORTER, status="pending")

        self.client.force_authenticate(self.reviewer)
        request_response = self.client.post(
            reverse("export-application-requests", args=[application.id]),
            {
                "reason": "Clarify exporter licence",
                "message": "Upload the current licence.",
                "items": ["licence"],
                "due_date": str(timezone.localdate() + timedelta(days=7)),
            },
            format="json",
        )
        self.assertEqual(request_response.status_code, 201)
        info_request = InformationRequest.objects.get(id=request_response.data["id"])
        application.refresh_from_db()
        self.assertEqual(application.status, "awaiting_information")

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("export-request-responses", args=[info_request.id]),
            {"message": "Attached.", "documents": [str(document.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        info_request.refresh_from_db()
        self.assertEqual(info_request.status, "responded")

        application.status = "under_review"
        application.save(update_fields=["status"])
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-request-review-response", args=[info_request.id]),
            {"accepted": True, "notes": "Accepted"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        info_request.refresh_from_db()
        self.assertEqual(info_request.status, "accepted")

    def test_request_and_condition_reject_past_due_dates(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-application-requests", args=[application.id]),
            {
                "reason": "Expired deadline",
                "message": "This should fail.",
                "items": ["licence"],
                "due_date": str(timezone.localdate() - timedelta(days=1)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("due_date", response.data["error"]["details"])

        response = self.client.post(
            reverse("export-application-conditions", args=[application.id]),
            {
                "title": "Past condition",
                "description": "This should fail.",
                "due_date": str(timezone.localdate() - timedelta(days=1)),
                "domain": Domain.EXPORTER,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("due_date", response.data["error"]["details"])

    def test_condition_requires_verified_current_evidence_before_clearance(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        condition = ExportCondition.objects.create(
            application=application,
            title="Upload quality certificate",
            description="Certificate required before approval.",
            due_date=timezone.localdate() + timedelta(days=10),
            domain=Domain.QUALITY,
        )
        self.add_document(application, Domain.QUALITY, condition=condition, status="pending")

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("export-condition-review", args=[condition.id]),
            {"notes": "Clear it"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

        ExportDocument.objects.filter(condition=condition).update(status="verified")
        response = self.client.post(
            reverse("export-condition-review", args=[condition.id]),
            {"notes": "Cleared"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        condition.refresh_from_db()
        self.assertIsNotNone(condition.cleared_at)

    def test_dashboard_risk_notifications_audit_and_reports(self):
        application = self.complete_application(self.make_application())
        Notification.objects.create(
            exporter=self.exporter,
            recipient=self.owner,
            title="Export update",
            body="A thing happened.",
        )

        self.client.force_authenticate(self.owner)
        dashboard = self.client.get(reverse("export-dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.data["totals"]["products"], 1)
        self.assertEqual(dashboard.data["totals"]["buyers"], 1)
        self.assertEqual(dashboard.data["totals"]["shipments"], 1)
        self.assertEqual(dashboard.data["unread_notifications"], 1)

        risk = self.client.get(reverse("export-risk"))
        self.assertEqual(risk.status_code, 200)
        self.assertEqual(risk.data["applications"][0]["application_id"], application.id)

        notifications = self.client.get(reverse("export-notification-list"))
        self.assertEqual(notifications.status_code, 200)
        notification_id = notifications.data["results"][0]["id"]
        mark_read = self.client.post(reverse("export-notification-mark-read", args=[notification_id]))
        self.assertEqual(mark_read.status_code, 200)
        self.assertIsNotNone(mark_read.data["read_at"])

        report = self.client.post(reverse("export-report-list"))
        self.assertEqual(report.status_code, 201)
        self.assertEqual(ExportReport.objects.count(), 1)
        audit = self.client.get(reverse("export-audit"))
        self.assertTrue(any(event["event_type"] == "export.register_exported" for event in audit.data["events"]))
        download = self.client.get(reverse("export-report-download", args=[report.data["id"]]))
        self.assertEqual(download.status_code, 200)
        self.assertIn("export-register", download["Content-Disposition"])

    def test_report_download_is_blocked_after_exporter_access_is_lost(self):
        self.complete_application(self.make_application())

        self.client.force_authenticate(self.owner)
        report = self.client.post(reverse("export-report-list"))
        self.assertEqual(report.status_code, 201)

        OrganisationMembership.objects.filter(organisation=self.org, user=self.owner).update(is_active=False)
        response = self.client.get(reverse("export-report-download", args=[report.data["id"]]))
        self.assertEqual(response.status_code, 403)

    def test_notification_list_is_isolated_to_recipient(self):
        Notification.objects.create(exporter=self.exporter, recipient=self.owner, title="Owner", body="Owner only")
        Notification.objects.create(exporter=self.exporter, recipient=self.reviewer, title="Reviewer", body="Reviewer only")

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("export-notification-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Owner")
