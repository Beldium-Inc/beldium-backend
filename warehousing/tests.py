from datetime import timedelta
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from warehousing import services
from warehousing.models import (
    Domain,
    DomainReview,
    Facility,
    InformationRequest,
    Inspection,
    InventoryLot,
    Notification,
    StorageZone,
    WarehouseOperator,
    WarehousingAccessGrant,
    WarehousingApplication,
    WarehousingCondition,
    WarehousingDocument,
    WarehousingReport,
)


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


class WarehousingTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@warehouse.test")
        self.reviewer = make_user("reviewer@warehouse.test")
        self.outsider = make_user("outsider@warehouse.test")
        self.org = make_org("Beldium Warehouse")
        self.other_org = make_org("Other Warehouse")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role=MembershipRole.OWNER)
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)
        self.warehouse = WarehouseOperator.objects.create(
            organisation=self.org,
            contact_name="Ada Bello",
            contact_email="warehouse@example.test",
            contact_phone="+2348000000000",
            services=["storage", "inventory handling"],
            storage_categories=["minerals", "general cargo"],
        )
        WarehousingAccessGrant.objects.create(warehouse=self.warehouse, user=self.reviewer, role="reviewer")

    def make_application(self, **overrides):
        application = WarehousingApplication.objects.create(warehouse=self.warehouse, created_by=self.owner, **overrides)
        services.initialise(application)
        return application

    def make_facility(self, **overrides):
        defaults = {
            "warehouse": self.warehouse,
            "name": "Ikeja Storage Depot",
            "facility_type": "ambient",
            "address": "1 Depot Road",
            "state": "Lagos",
            "capacity": "5000.00",
            "fire_certificate_expires_on": timezone.localdate() + timedelta(days=120),
            "insurance_expires_on": timezone.localdate() + timedelta(days=120),
        }
        return Facility.objects.create(**{**defaults, **overrides})

    def make_zone(self, **overrides):
        facility = overrides.pop("facility", None) or self.make_facility()
        defaults = {
            "warehouse": self.warehouse,
            "facility": facility,
            "name": "Zone A",
            "storage_type": "ambient bulk",
            "capacity": "1000.00",
        }
        return StorageZone.objects.create(**{**defaults, **overrides})

    def make_lot(self, **overrides):
        facility = overrides.pop("facility", None) or self.make_facility()
        zone = overrides.pop("zone", None) or self.make_zone(facility=facility)
        defaults = {
            "warehouse": self.warehouse,
            "facility": facility,
            "zone": zone,
            "product_name": "Lithium ore",
            "batch_number": "BATCH-001",
            "owner_name": "Beldium Mining",
            "quantity": "50.00",
            "received_on": timezone.localdate(),
            "expires_on": timezone.localdate() + timedelta(days=180),
            "status": "stored",
        }
        return InventoryLot.objects.create(**{**defaults, **overrides})

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
        return WarehousingDocument.objects.create(**{**defaults, **overrides})

    def complete_application(self, application):
        lot = self.make_lot()
        Inspection.objects.create(
            warehouse=self.warehouse,
            facility=lot.facility,
            inspection_type="fire_safety",
            inspected_on=timezone.localdate(),
            inspector_name="Safety Desk",
            outcome="passed",
            next_due_on=timezone.localdate() + timedelta(days=90),
        )
        DomainReview.objects.filter(application=application, applicable=True).update(
            data={"declared": True},
            status="passed",
            score=90,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )
        for key in application.sections.filter(applicable=True).values_list("key", flat=True):
            self.add_document(application, key, lot=lot if key == Domain.INVENTORY else None)
        return application


class WarehousingAudienceTests(WarehousingTestCase):
    def test_capabilities_are_resolved_from_membership_and_grants(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("warehousing-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["warehouses"][0]["can_edit"])
        self.assertFalse(response.data["warehouses"][0]["can_review"])

        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("warehousing-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["warehouses"][0]["can_review"])
        self.assertFalse(response.data["warehouses"][0]["can_edit"])

    def test_user_does_not_see_other_warehouses(self):
        WarehouseOperator.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="other@example.test",
            contact_phone="+2348111111111",
            services=["storage"],
            storage_categories=["cold chain"],
        )
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("warehousing-warehouse-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.warehouse.id))

    def test_access_grants_are_admin_only_and_can_be_updated(self):
        staff = make_user("staff@warehouse.test", is_staff=True)
        new_reviewer = make_user("new-reviewer@warehouse.test")
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("warehousing-access-grant-list"),
            {"warehouse": str(self.warehouse.id), "user": str(new_reviewer.id), "role": "reviewer"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(staff)
        response = self.client.post(
            reverse("warehousing-access-grant-list"),
            {"warehouse": str(self.warehouse.id), "user": str(new_reviewer.id), "role": "reviewer"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        response = self.client.patch(reverse("warehousing-access-grant-detail", args=[response.data["id"]]), {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])

    def test_operator_serializer_rejects_duplicate_services_and_categories(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(reverse("warehousing-warehouse-detail", args=[self.warehouse.id]), {"services": ["Storage", "storage"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("services", response.data["error"]["details"])
        response = self.client.patch(reverse("warehousing-warehouse-detail", args=[self.warehouse.id]), {"storage_categories": ["Ore", "ore"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("storage_categories", response.data["error"]["details"])

    def test_reviewer_grant_is_read_only_for_warehouse_profile(self):
        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("warehousing-warehouse-list"))
        self.assertEqual(response.status_code, 200)

        response = self.client.patch(
            reverse("warehousing-warehouse-detail", args=[self.warehouse.id]),
            {"contact_name": "Changed"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_outsider_cannot_create_warehouse_for_unmanaged_organisation(self):
        unmanaged_org = make_org("Unmanaged Warehouse Org")
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            reverse("warehousing-warehouse-list"),
            {
                "organisation": str(unmanaged_org.id),
                "contact_name": "Intruder",
                "contact_email": "intruder@example.test",
                "contact_phone": "+2348000000001",
                "services": ["storage"],
                "storage_categories": ["general"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class WarehousingWorkflowTests(WarehousingTestCase):
    def test_application_creation_lays_down_warehouse_domains(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("warehousing-application-list"), {"warehouse": str(self.warehouse.id)}, format="json")
        self.assertEqual(response.status_code, 201)
        application = WarehousingApplication.objects.get(id=response.data["id"])
        self.assertEqual(application.sections.count(), len(Domain.choices))

    def test_submission_requires_records_sections_and_documents(self):
        application = self.make_application()
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("warehousing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "application_incomplete")

        self.make_lot()
        DomainReview.objects.filter(application=application).update(data={"declared": True})
        response = self.client.post(reverse("warehousing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertIn("operator", response.data["error"]["details"]["missing_document_domains"])

    def test_facility_zone_lot_and_inspection_crud_use_owner_permissions(self):
        self.client.force_authenticate(self.owner)
        facility_response = self.client.post(
            reverse("warehousing-facility-list"),
            {
                "warehouse": str(self.warehouse.id),
                "name": "Cold Store",
                "facility_type": "cold_chain",
                "address": "2 Depot Road",
                "state": "Lagos",
                "capacity": "800.00",
            },
            format="json",
        )
        self.assertEqual(facility_response.status_code, 201)
        zone_response = self.client.post(
            reverse("warehousing-zone-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": facility_response.data["id"],
                "name": "Cold A",
                "storage_type": "cold",
                "temperature_min": "2.00",
                "temperature_max": "8.00",
                "capacity": "100.00",
            },
            format="json",
        )
        self.assertEqual(zone_response.status_code, 201)
        lot_response = self.client.post(
            reverse("warehousing-lot-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": facility_response.data["id"],
                "zone": zone_response.data["id"],
                "product_name": "Tin concentrate",
                "batch_number": "TIN-001",
                "owner_name": "Tin Owner",
                "quantity": "10.00",
                "received_on": str(timezone.localdate()),
                "status": "stored",
            },
            format="json",
        )
        self.assertEqual(lot_response.status_code, 201)
        inspection_response = self.client.post(
            reverse("warehousing-inspection-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": facility_response.data["id"],
                "inspection_type": "fire_safety",
                "inspected_on": str(timezone.localdate()),
                "inspector_name": "Safety Desk",
                "outcome": "passed",
            },
            format="json",
        )
        self.assertEqual(inspection_response.status_code, 201)

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("warehousing-facility-list"),
            {
                "warehouse": str(self.warehouse.id),
                "name": "Blocked Depot",
                "facility_type": "ambient",
                "address": "Blocked",
                "state": "Lagos",
                "capacity": "10.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_list_filters_and_search_work_for_warehouse_records(self):
        facility = self.make_facility(name="Ambient Depot", facility_type="ambient")
        zone = self.make_zone(facility=facility, name="Bulk A", restricted=True)
        self.make_lot(facility=facility, zone=zone, product_name="Lithium ore", status="stored")

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("warehousing-facility-list"), {"facility_type": "ambient"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        response = self.client.get(reverse("warehousing-zone-list"), {"restricted": "true"})
        self.assertEqual(response.data["count"], 1)
        response = self.client.get(reverse("warehousing-lot-list"), {"search": "Lithium"})
        self.assertEqual(response.data["count"], 1)

    def test_cross_warehouse_zone_and_lot_validation(self):
        other_warehouse = WarehouseOperator.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="other@example.test",
            contact_phone="+2348111111111",
            services=["storage"],
            storage_categories=["general"],
        )
        other_facility = Facility.objects.create(warehouse=other_warehouse, name="Other Depot", facility_type="ambient", address="Other", state="Oyo", capacity="10.00")
        facility = self.make_facility()

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("warehousing-zone-list"),
            {"warehouse": str(self.warehouse.id), "facility": str(other_facility.id), "name": "Bad", "storage_type": "ambient", "capacity": "1.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("facility", response.data["error"]["details"])

        other_zone = StorageZone.objects.create(warehouse=other_warehouse, facility=other_facility, name="Other Zone", storage_type="ambient", capacity="5.00")
        response = self.client.post(
            reverse("warehousing-lot-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": str(facility.id),
                "zone": str(other_zone.id),
                "product_name": "Bad Lot",
                "batch_number": "BAD-1",
                "owner_name": "Owner",
                "quantity": "1.00",
                "received_on": str(timezone.localdate()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("zone", response.data["error"]["details"])

    def test_record_date_and_temperature_validation(self):
        facility = self.make_facility()
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("warehousing-zone-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": str(facility.id),
                "name": "Bad Temp",
                "storage_type": "cold",
                "temperature_min": "8.00",
                "temperature_max": "2.00",
                "capacity": "1.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("temperature_max", response.data["error"]["details"])

        zone = self.make_zone(facility=facility)
        response = self.client.post(
            reverse("warehousing-lot-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": str(facility.id),
                "zone": str(zone.id),
                "product_name": "Expired",
                "batch_number": "EXP-1",
                "owner_name": "Owner",
                "quantity": "1.00",
                "received_on": str(timezone.localdate()),
                "expires_on": str(timezone.localdate() - timedelta(days=1)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("expires_on", response.data["error"]["details"])

        response = self.client.post(
            reverse("warehousing-inspection-list"),
            {
                "warehouse": str(self.warehouse.id),
                "facility": str(facility.id),
                "inspection_type": "fire_safety",
                "inspected_on": str(timezone.localdate()),
                "inspector_name": "Safety Desk",
                "next_due_on": str(timezone.localdate() - timedelta(days=1)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("next_due_on", response.data["error"]["details"])

    def test_owner_can_update_section_upload_document_and_replace_version(self):
        application = self.make_application()
        lot = self.make_lot()
        self.client.force_authenticate(self.owner)
        response = self.client.patch(reverse("warehousing-application-section", args=[application.id, Domain.INVENTORY]), {"data": {"fifo": True}}, format="json")
        self.assertEqual(response.status_code, 200)

        first = self.client.post(
            reverse("warehousing-application-documents", args=[application.id]),
            {"domain": Domain.INVENTORY, "document_type": "inventory_register", "title": "Inventory register", "lot": str(lot.id), "file": SimpleUploadedFile("v1.pdf", b"%PDF-1.4", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(first.status_code, 201)
        second = self.client.post(
            reverse("warehousing-application-documents", args=[application.id]),
            {"domain": Domain.INVENTORY, "document_type": "inventory_register", "title": "Inventory register v2", "lot": str(lot.id), "file": SimpleUploadedFile("v2.pdf", b"%PDF-1.4", content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.data["version"], 2)
        self.assertFalse(WarehousingDocument.objects.get(id=first.data["id"]).is_current)

    def test_document_version_rejects_identity_changes(self):
        application = self.make_application()
        self.add_document(application, Domain.OPERATOR, document_type="shared_record", status="pending")

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("warehousing-application-documents", args=[application.id]),
            {
                "domain": Domain.FACILITY,
                "document_type": "shared_record",
                "title": "Changed identity",
                "file": SimpleUploadedFile("changed.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "document_identity_changed")

    def test_document_upload_validation_and_expiring_endpoint(self):
        application = self.make_application()
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("warehousing-application-documents", args=[application.id]),
            {"domain": Domain.OPERATOR, "document_type": "license", "title": "License", "file": SimpleUploadedFile("bad.txt", b"bad", content_type="text/plain")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.data["error"]["details"])

        due = self.add_document(application, Domain.OPERATOR, expires_on=timezone.localdate() + timedelta(days=5))
        self.add_document(application, Domain.FACILITY, expires_on=timezone.localdate() + timedelta(days=90))
        response = self.client.get(reverse("warehousing-document-expiring"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], str(due.id))

    def test_document_upload_rejects_bad_dates_and_cross_warehouse_lot(self):
        application = self.make_application()
        other_warehouse = WarehouseOperator.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="other@example.test",
            contact_phone="+2348111111111",
            services=["storage"],
            storage_categories=["general"],
        )
        other_facility = Facility.objects.create(
            warehouse=other_warehouse,
            name="Other Depot",
            facility_type="ambient",
            address="Other",
            state="Oyo",
            capacity="10.00",
        )
        other_zone = StorageZone.objects.create(
            warehouse=other_warehouse,
            facility=other_facility,
            name="Other Zone",
            storage_type="ambient",
            capacity="5.00",
        )
        other_lot = InventoryLot.objects.create(
            warehouse=other_warehouse,
            facility=other_facility,
            zone=other_zone,
            product_name="Other Lot",
            batch_number="OTHER-1",
            owner_name="Other Owner",
            quantity="1.00",
            received_on=timezone.localdate(),
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("warehousing-application-documents", args=[application.id]),
            {
                "domain": Domain.OPERATOR,
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

        response = self.client.post(
            reverse("warehousing-application-documents", args=[application.id]),
            {
                "domain": Domain.INVENTORY,
                "document_type": "lot_record",
                "title": "Lot record",
                "lot": str(other_lot.id),
                "file": SimpleUploadedFile("lot.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("lot", response.data["error"]["details"])

    def test_document_review_download_and_expired_evidence_rejection(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        document = self.add_document(application, Domain.OPERATOR, status="pending")
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("warehousing-document-review", args=[document.id]), {"status": "verified", "notes": "Ok"}, format="json")
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse("warehousing-document-download", args=[document.id]))
        self.assertEqual(response.status_code, 200)

        expired = self.add_document(application, Domain.FACILITY, status="pending", expires_on=timezone.localdate() - timedelta(days=1))
        response = self.client.post(reverse("warehousing-document-review", args=[expired.id]), {"status": "verified", "notes": "Expired"}, format="json")
        self.assertEqual(response.status_code, 409)

    def test_reviewer_assignment_start_review_domain_review_and_decisions(self):
        application = self.complete_application(self.make_application(status="submitted"))
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("warehousing-application-assign-reviewer", args=[application.id]), {"reviewer": str(self.reviewer.id)}, format="json")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("warehousing-application-start-review", args=[application.id]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("warehousing-application-review-section", args=[application.id, Domain.OPERATOR]), {"status": "passed", "score": 95, "notes": "Ok", "applicable": True}, format="json")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("warehousing-application-decide", args=[application.id]), {"status": "approved", "rationale": "Approved"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "approved")

    def test_unassigned_reviewer_cannot_start_review(self):
        application = self.make_application(status="submitted")
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("warehousing-application-start-review", args=[application.id]))
        self.assertEqual(response.status_code, 403)

    def test_rejected_and_conditional_decision_paths(self):
        rejected = self.make_application(status="under_review", reviewer=self.reviewer)
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("warehousing-application-decide", args=[rejected.id]), {"status": "rejected", "rationale": "Incomplete"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "rejected")
        rejected.delete()

        conditional = self.complete_application(self.make_application(status="under_review", reviewer=self.reviewer))
        conditional.sections.filter(key=Domain.SAFETY).update(status="attention")
        response = self.client.post(
            reverse("warehousing-application-decide", args=[conditional.id]),
            {
                "status": "conditionally_approved",
                "rationale": "Safety follow-up needed",
                "conditions": [{
                    "title": "Submit fire drill record",
                    "description": "Upload latest drill record.",
                    "due_date": str(timezone.localdate() + timedelta(days=14)),
                    "domain": Domain.SAFETY,
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "conditionally_approved")

    def test_conditional_approval_requires_condition(self):
        application = self.complete_application(self.make_application(status="under_review", reviewer=self.reviewer))
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("warehousing-application-decide", args=[application.id]),
            {"status": "conditionally_approved", "rationale": "Needs follow-up"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

    def test_information_request_response_and_condition_clearance(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        document = self.add_document(application, Domain.OPERATOR, status="verified")
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("warehousing-application-requests", args=[application.id]),
            {"reason": "Clarify licence", "message": "Upload support.", "items": ["licence"], "due_date": str(timezone.localdate() + timedelta(days=7))},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        info_request = InformationRequest.objects.get(id=response.data["id"])
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("warehousing-request-responses", args=[info_request.id]), {"message": "Attached.", "documents": [str(document.id)]}, format="json")
        self.assertEqual(response.status_code, 201)

        application.status = "under_review"
        application.save(update_fields=["status"])
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("warehousing-request-review-response", args=[info_request.id]), {"accepted": True, "notes": "Accepted"}, format="json")
        self.assertEqual(response.status_code, 200)

        condition = WarehousingCondition.objects.create(application=application, title="Calibrate scale", description="Upload certificate.", due_date=timezone.localdate() + timedelta(days=10), domain=Domain.EQUIPMENT)
        self.add_document(application, Domain.EQUIPMENT, condition=condition, status="verified")
        response = self.client.post(reverse("warehousing-condition-review", args=[condition.id]), {"notes": "Cleared"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_request_and_condition_reject_past_due_dates(self):
        application = self.make_application(status="under_review", reviewer=self.reviewer)
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("warehousing-application-requests", args=[application.id]),
            {
                "reason": "Past deadline",
                "message": "This should fail.",
                "items": ["licence"],
                "due_date": str(timezone.localdate() - timedelta(days=1)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("due_date", response.data["error"]["details"])

        response = self.client.post(
            reverse("warehousing-application-conditions", args=[application.id]),
            {
                "title": "Past condition",
                "description": "This should fail.",
                "due_date": str(timezone.localdate() - timedelta(days=1)),
                "domain": Domain.OPERATOR,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("due_date", response.data["error"]["details"])

    def test_dashboard_notifications_audit_and_report_access_loss(self):
        application = self.complete_application(self.make_application())
        Notification.objects.create(warehouse=self.warehouse, recipient=self.owner, title="Owner", body="Owner only")
        Notification.objects.create(warehouse=self.warehouse, recipient=self.reviewer, title="Reviewer", body="Reviewer only")
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("warehousing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 200)
        dashboard = self.client.get(reverse("warehousing-dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.data["totals"]["facilities"], 1)
        self.assertEqual(dashboard.data["totals"]["zones"], 1)
        self.assertEqual(dashboard.data["totals"]["lots"], 1)
        notifications = self.client.get(reverse("warehousing-notification-list"))
        self.assertEqual(notifications.data["count"], 2)
        report = self.client.post(reverse("warehousing-report-list"))
        self.assertEqual(report.status_code, 201)
        self.assertEqual(WarehousingReport.objects.count(), 1)
        audit = self.client.get(reverse("warehousing-audit"))
        self.assertEqual(audit.status_code, 200)
        OrganisationMembership.objects.filter(organisation=self.org, user=self.owner).update(is_active=False)
        response = self.client.get(reverse("warehousing-report-download", args=[report.data["id"]]))
        self.assertEqual(response.status_code, 403)

    def test_notification_list_is_isolated_to_recipient_and_risk_endpoint_works(self):
        application = self.complete_application(self.make_application())
        Notification.objects.create(warehouse=self.warehouse, recipient=self.owner, title="Owner", body="Owner only")
        Notification.objects.create(warehouse=self.warehouse, recipient=self.reviewer, title="Reviewer", body="Reviewer only")

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("warehousing-notification-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Owner")

        response = self.client.get(reverse("warehousing-risk"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["applications"][0]["application_id"], application.id)
