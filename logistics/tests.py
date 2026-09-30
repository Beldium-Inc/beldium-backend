from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from logistics import services
from logistics.models import (
    ApprovalCondition,
    Domain,
    DomainReview,
    Driver,
    InformationRequest,
    LogisticsAccessGrant,
    LogisticsApplication,
    LogisticsCompany,
    LogisticsDocument,
    LogisticsReport,
    LogisticsPayment,
    LogisticsTransaction,
    MonitoringEvent,
    Movement,
    TransportRequest,
    Delivery,
    Incident,
    ActionItem,
    OperationsEvent,
    OperationsDocument,
    ComplianceFinding,
    Notification,
    ScopeRestriction,
    Vehicle,
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


class LogisticsTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@haulage.test")
        self.reviewer = make_user("reviewer@desk.test")
        self.regulator = make_user("regulator@oversight.test")
        self.outsider = make_user("outsider@other.test")

        self.org = make_org("Ilesa Heavy Haulage")
        self.other_org = make_org("Kaduna General Freight")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role=MembershipRole.OWNER)
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)

        self.company = LogisticsCompany.objects.create(
            organisation=self.org,
            contact_name="Ada Bello",
            contact_email="ops@ilesa.example",
            contact_phone="+2348000000000",
            employees=25,
            annual_tonnage="5000.00",
            services=["mineral haulage", "general freight"],
        )
        LogisticsAccessGrant.objects.create(company=self.company, user=self.reviewer, role="reviewer")
        LogisticsAccessGrant.objects.create(company=self.company, user=self.regulator, role="regulator")

    def make_application(self, **overrides):
        application = LogisticsApplication.objects.create(
            company=self.company,
            created_by=self.owner,
            **overrides,
        )
        services.initialise(application)
        return application

    def make_vehicle(self, **overrides):
        defaults = {
            "company": self.company,
            "registration": "LAG-123-XY",
            "vin": "VIN00000000000001",
            "vehicle_type": "Truck",
            "make": "MAN",
            "model": "TGS",
            "year": timezone.localdate().year,
            "capacity": "30.00",
            "capacity_unit": "tonnes",
            "ownership": "owned",
            "insurance_expiry": timezone.localdate() + timedelta(days=120),
            "roadworthiness_expiry": timezone.localdate() + timedelta(days=120),
        }
        return Vehicle.objects.create(**{**defaults, **overrides})

    def make_driver(self, **overrides):
        defaults = {
            "company": self.company,
            "full_name": "Musa Lawal",
            "licence_number": "DRV-12345",
            "licence_class": "G",
            "licence_expiry": timezone.localdate() + timedelta(days=120),
            "medical_expiry": timezone.localdate() + timedelta(days=120),
        }
        return Driver.objects.create(**{**defaults, **overrides})

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
        return LogisticsDocument.objects.create(**{**defaults, **overrides})

    def complete_application(self, application):
        self.make_vehicle()
        self.make_driver()
        DomainReview.objects.filter(application=application, applicable=True).update(
            data={"declared": True},
            status="passed",
            score=90,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )
        for key in application.sections.filter(applicable=True).values_list("key", flat=True):
            self.add_document(application, key)
        return application


class LogisticsAudienceTests(LogisticsTestCase):
    def test_capabilities_are_resolved_from_membership_and_grants(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("logistics-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["companies"][0]["can_edit"])
        self.assertFalse(response.data["companies"][0]["can_review"])

        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("logistics-me"))
        self.assertTrue(response.data["companies"][0]["can_review"])
        self.assertFalse(response.data["companies"][0]["can_edit"])

    def test_company_user_does_not_see_other_logistics_companies(self):
        LogisticsCompany.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="ops@kaduna.example",
            contact_phone="+2348111111111",
            services=["general freight"],
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("logistics-company-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.company.id))

    def test_regulator_grant_is_read_only(self):
        self.client.force_authenticate(self.regulator)
        response = self.client.get(reverse("logistics-company-list"))
        self.assertEqual(response.status_code, 200)
        response = self.client.patch(
            reverse("logistics-company-detail", args=[self.company.id]),
            {"contact_name": "Changed"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class LogisticsWorkflowTests(LogisticsTestCase):
    def test_application_creation_lays_down_nine_domains(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-application-list"),
            {"company": str(self.company.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        application = LogisticsApplication.objects.get(id=response.data["id"])
        self.assertEqual(application.sections.count(), len(Domain.choices))
        self.assertTrue(application.sections.get(key=Domain.MINERAL).applicable)

    def test_submission_requires_profile_records_section_data_and_documents(self):
        application = self.make_application()
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("logistics-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "application_incomplete")

        self.make_vehicle()
        self.make_driver()
        DomainReview.objects.filter(application=application).update(data={"declared": True})
        response = self.client.post(reverse("logistics-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertIn("corporate", response.data["error"]["details"]["missing_document_domains"])

    def test_driver_evidence_is_redacted_for_regulator_grants(self):
        application = self.make_application()
        driver = self.make_driver(national_id="NIN-SECRET")
        document = LogisticsDocument.objects.create(
            application=application,
            domain=Domain.DRIVER,
            document_type="driver_licence",
            title="Driver licence",
            driver=driver,
            file=SimpleUploadedFile("licence.pdf", b"%PDF-1.4", content_type="application/pdf"),
            original_name="licence.pdf",
            uploaded_by=self.owner,
        )

        self.client.force_authenticate(self.regulator)
        self.assertEqual(self.client.get(reverse("logistics-document-detail", args=[document.id])).status_code, 404)
        response = self.client.get(reverse("logistics-driver-detail", args=[driver.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("national_id", response.data)
        self.assertNotIn("licence_number", response.data)

    def test_expiry_monitor_is_idempotent_and_applies_scope_restriction(self):
        application = self.make_application(status="approved")
        document = LogisticsDocument.objects.create(
            application=application,
            domain=Domain.REGULATORY,
            document_type="haulage_permit",
            title="Haulage permit",
            expires_on=timezone.localdate() - timedelta(days=1),
            service_scope="mineral haulage",
            status="verified",
            file=SimpleUploadedFile("permit.pdf", b"%PDF-1.4", content_type="application/pdf"),
            original_name="permit.pdf",
            uploaded_by=self.owner,
        )

        self.assertEqual(services.monitor_expiries()["new_events"], 1)
        self.assertEqual(services.monitor_expiries()["new_events"], 0)
        self.assertEqual(MonitoringEvent.objects.filter(document=document, kind="expired").count(), 1)
        self.assertEqual(
            ScopeRestriction.objects.filter(
                company=self.company,
                source_document=document,
                automatic=True,
                resolved_at__isnull=True,
            ).count(),
            1,
        )

    def test_reviewer_assignment_start_review_and_duplicate_decision_guards(self):
        application = self.complete_application(self.make_application(status="submitted"))
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("logistics-application-assign-reviewer", args=[application.id]),
            {"reviewer": str(self.reviewer.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("logistics-application-start-review", args=[application.id]))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("logistics-application-decide", args=[application.id]),
            {"status": "approved", "rationale": "All required domains passed."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "approved")

        application.refresh_from_db()
        application.status = "under_review"
        application.save(update_fields=["status", "updated_at"])
        response = self.client.post(
            reverse("logistics-application-decide", args=[application.id]),
            {"status": "under_review", "rationale": "Invalid decision value."},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_conditional_approval_creates_condition_and_restriction(self):
        application = self.complete_application(self.make_application(status="under_review", reviewer=self.reviewer))
        application.sections.filter(key=Domain.INSURANCE).update(status="attention", score=70)

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("logistics-application-decide", args=[application.id]),
            {
                "status": "conditionally_approved",
                "rationale": "Insurance renewal evidence is acceptable with a scope condition.",
                "conditions": [{
                    "title": "Renew goods-in-transit policy",
                    "description": "Provide updated cover for mineral haulage.",
                    "due_date": str(timezone.localdate() + timedelta(days=14)),
                    "service_scope": "mineral haulage",
                }],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "conditionally_approved")
        self.assertEqual(ApprovalCondition.objects.filter(application=application).count(), 1)
        self.assertEqual(ScopeRestriction.objects.filter(company=self.company, service_scope="mineral haulage").count(), 1)

    def test_document_renewal_after_approval_versions_existing_credential(self):
        application = self.make_application(status="approved")
        original = self.add_document(
            application,
            Domain.REGULATORY,
            document_type="haulage_permit",
            service_scope="mineral haulage",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-application-documents", args=[application.id]),
            {
                "domain": Domain.REGULATORY,
                "document_type": "haulage_permit",
                "title": "Renewed haulage permit",
                "service_scope": "mineral haulage",
                "expires_on": str(timezone.localdate() + timedelta(days=365)),
                "file": SimpleUploadedFile("renewal.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        original.refresh_from_db()
        self.assertFalse(original.is_current)
        self.assertEqual(response.data["version"], 2)
        application.refresh_from_db()
        self.assertEqual(application.status, "awaiting_information")

    def test_information_request_response_requires_verified_current_evidence(self):
        application = self.make_application(status="awaiting_information", reviewer=self.reviewer)
        request_item = InformationRequest.objects.create(
            application=application,
            reason="Missing insurance evidence",
            message="Upload corrected policy.",
            items=["insurance policy"],
            due_date=timezone.localdate() + timedelta(days=7),
            raised_by=self.reviewer,
        )
        rejected = self.add_document(application, Domain.INSURANCE, status="rejected")

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-request-responses", args=[request_item.id]),
            {"message": "Attached.", "documents": [str(rejected.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 409)

        verified = self.add_document(application, Domain.INSURANCE, document_type="insurance_policy")
        response = self.client.post(
            reverse("logistics-request-responses", args=[request_item.id]),
            {"message": "Corrected evidence attached.", "documents": [str(verified.id)]},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        request_item.refresh_from_db()
        self.assertEqual(request_item.status, "responded")

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("logistics-request-review-response", args=[request_item.id]),
            {"accepted": True, "notes": "Accepted."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "accepted")

    def test_report_download_is_revoked_if_company_access_changes(self):
        report = LogisticsReport.objects.create(
            requested_by=self.owner,
            company_ids=[str(self.company.id)],
            content="Reference,Company\nBLD-LOG-1,Ilesa\n",
        )
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(reverse("logistics-report-download", args=[report.id])).status_code, 200)

        OrganisationMembership.objects.filter(organisation=self.org, user=self.owner).update(is_active=False)
        response = self.client.get(reverse("logistics-report-download", args=[report.id]))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_exposes_frontend_summary_metrics(self):
        application = self.make_application(status="conditionally_approved")
        self.make_vehicle()
        self.make_driver()
        InformationRequest.objects.create(
            application=application,
            reason="Upload renewal",
            message="Please upload the renewal.",
            items=["renewal"],
            due_date=timezone.localdate() + timedelta(days=7),
            raised_by=self.reviewer,
        )
        self.add_document(application, Domain.REGULATORY, expires_on=timezone.localdate() + timedelta(days=10))
        ScopeRestriction.objects.create(company=self.company, service_scope="mineral haulage", reason="Condition pending")
        MonitoringEvent.objects.create(company=self.company, event_key="demo-alert", kind="expiring", message="Permit expires soon.")

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("logistics-dashboard"))
        self.assertEqual(response.status_code, 200)
        company = response.data["companies"][0]
        self.assertEqual(company["expiring_documents"], 1)
        self.assertEqual(company["open_alerts"], 1)
        self.assertEqual(company["open_requests"], 1)
        self.assertEqual(response.data["totals"]["restricted_scopes"], 1)

    def test_condition_evidence_endpoint_links_document_to_condition(self):
        application = self.make_application(status="conditionally_approved")
        condition = ApprovalCondition.objects.create(
            application=application,
            title="Renew mineral haulage cover",
            description="Upload renewed goods-in-transit cover.",
            due_date=timezone.localdate() + timedelta(days=7),
            service_scope="mineral haulage",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-condition-evidence", args=[condition.id]),
            {
                "domain": Domain.INSURANCE,
                "document_type": "condition_insurance_cover",
                "title": "Renewed condition cover",
                "expires_on": str(timezone.localdate() + timedelta(days=365)),
                "file": SimpleUploadedFile("condition-cover.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["condition"]), str(condition.id))
        self.assertEqual(response.data["service_scope"], "mineral haulage")
        self.assertEqual(response.data["version"], 1)

        response = self.client.post(
            reverse("logistics-condition-evidence", args=[condition.id]),
            {
                "domain": Domain.INSURANCE,
                "document_type": "condition_insurance_cover",
                "title": "Renewed condition cover v2",
                "expires_on": str(timezone.localdate() + timedelta(days=400)),
                "file": SimpleUploadedFile("condition-cover-v2.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["version"], 2)
        self.assertEqual(
            LogisticsDocument.objects.filter(condition=condition, document_type="condition_insurance_cover", is_current=True).count(),
            1,
        )

    def test_condition_evidence_endpoint_rejects_cleared_condition(self):
        application = self.make_application(status="conditionally_approved")
        condition = ApprovalCondition.objects.create(
            application=application,
            title="Renew mineral haulage cover",
            description="Upload renewed goods-in-transit cover.",
            due_date=timezone.localdate() + timedelta(days=7),
            service_scope="mineral haulage",
            cleared_at=timezone.now(),
            cleared_by=self.reviewer,
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-condition-evidence", args=[condition.id]),
            {
                "domain": Domain.INSURANCE,
                "document_type": "condition_insurance_cover",
                "title": "Late condition cover",
                "file": SimpleUploadedFile("condition-cover.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "condition_already_cleared")


class LogisticsOperationsPortalTests(LogisticsTestCase):
    def make_ops_vehicle_driver(self):
        vehicle = self.make_vehicle(registration="LG-220", vin="VIN00000000000220")
        driver = self.make_driver(full_name="Halima Sule", licence_number="NGA-DL-220")
        driver.assigned_vehicle = vehicle
        driver.save(update_fields=["assigned_vehicle", "updated_at"])
        return vehicle, driver

    def make_movement(self, **overrides):
        defaults = {
            "company": self.company,
            "reference": "MOV-1042",
            "batch_id": "BATCH-NL024-07",
            "rfq_id": "RFQ-8841",
            "transaction_id": "TXN-5521",
            "movement_type": "bulk",
            "miner": "Nasarawa Lithium Coop",
            "buyer": "Zhen Hua Metals",
            "mineral": "Lithium Concentrate",
            "quantity": "32.000",
            "quantity_unit": "MT",
            "origin": "Mine NL-024",
            "destination": "Pyramid Processing Plant",
            "status": "scheduled",
            "pickup_at": timezone.now(),
        }
        return Movement.objects.create(**{**defaults, **overrides})

    def test_operations_records_are_scoped_to_accessible_companies(self):
        Movement.objects.create(
            company=self.company,
            reference="MOV-1001",
            movement_type="sample",
            mineral="Lithium",
            quantity="0.008",
            quantity_unit="kg",
            origin="Mine",
            destination="Lab",
            status="in_transit",
        )
        other_company = LogisticsCompany.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="ops@other.example",
            contact_phone="+2348111111111",
            services=["general freight"],
        )
        Movement.objects.create(
            company=other_company,
            reference="MOV-OTHER",
            movement_type="bulk",
            mineral="Barite",
            origin="Warehouse",
            destination="Port",
            status="delivered",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("logistics-movement-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["reference"], "MOV-1001")

    def test_operations_dashboard_matches_portal_summary_needs(self):
        vehicle, driver = self.make_ops_vehicle_driver()
        movement = self.make_movement(vehicle=vehicle, driver=driver, status="in_transit")
        TransportRequest.objects.create(
            company=self.company,
            rfq_id="RFQ-8902",
            transaction_id="TXN-5560",
            movement_type="bulk",
            requester="Export Desk",
            miner="Jos Tin Collective",
            buyer="Baltic Ore AG",
            mineral="Cassiterite",
            quantity="24.000",
            origin="Mine JS-011",
            destination="Warehouse ABJ-2",
        )
        ActionItem.objects.create(company=self.company, action="Assign Vehicle", target="TR-2296", urgency="Today")
        OperationsEvent.objects.create(company=self.company, occurred_at=timezone.now(), sector="Tracking", event_type="Tracking", text="Checkpoint recorded.", unread=True)
        ComplianceFinding.objects.create(company=self.company, area="Driver", detail="Training overdue", action="Schedule refresher", status="open")
        Notification.objects.create(company=self.company, recipient=self.owner, title="Compliance", body="Review requested")
        LogisticsTransaction.objects.create(
            company=self.company,
            transaction_id="TXN-5521",
            buyer="Zhen Hua Metals",
            miner="Nasarawa Lithium Coop",
            material="Lithium",
            quantity="32.000",
            origin="Mine NL-024",
            destination="Pyramid Processing",
            movement=movement,
            transport_fee="1840000.00",
            stage="Bulk logistics - in transit",
            delivery_status="In transit",
            payment_status="50% advanced",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("logistics-operations_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stats"]["active_jobs"], 1)
        self.assertEqual(response.data["stats"]["new_transport_requests"], 1)
        self.assertEqual(response.data["stats"]["unread_notifications"], 2)
        self.assertNotIn("awaiting_acceptance", response.data["stats"])
        self.assertEqual(response.data["action_items"][0]["action"], "Assign Vehicle")
        self.assertEqual(response.data["active_movements"][0]["vehicle_registration"], "LG-220")

    def test_movement_assignment_and_status_update(self):
        vehicle, driver = self.make_ops_vehicle_driver()
        movement = self.make_movement()

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-movement-assign", args=[movement.id]),
            {"vehicle": str(vehicle.id), "driver": str(driver.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "assigned")
        self.assertEqual(response.data["vehicle_registration"], "LG-220")

        response = self.client.post(
            reverse("logistics-movement-set-status", args=[movement.id]),
            {"status": "in_transit", "latitude": "9.076500", "longitude": "7.398600", "note": "Truck dispatched."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "in_transit")
        self.assertEqual(OperationsEvent.objects.filter(company=self.company, text="Truck dispatched.").count(), 1)

    def test_delivery_completion_and_incident_resolution(self):
        movement = self.make_movement(status="in_transit")
        delivery = Delivery.objects.create(
            company=self.company,
            movement=movement,
            destination_type="Processor",
            destination="Pyramid Processing Plant",
            expected_quantity="32.000",
            quantity_unit="MT",
            status="in_transit",
        )
        incident = Incident.objects.create(
            company=self.company,
            movement=movement,
            incident_type="Quantity Discrepancy",
            severity="medium",
            status="open",
            occurred_at=timezone.now(),
            description="Weighbridge discrepancy.",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-delivery-complete", args=[delivery.id]),
            {"received_quantity": "32.000", "receipt_reference": "PR-2209"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["variance"], Decimal("0.000"))

        short_delivery = Delivery.objects.create(
            company=self.company,
            movement=movement,
            destination_type="Processor",
            destination="Pyramid Processing Plant",
            expected_quantity="32.000",
            quantity_unit="MT",
            status="arrived",
        )
        response = self.client.post(
            reverse("logistics-delivery-complete", args=[short_delivery.id]),
            {"received_quantity": "31.920", "receipt_reference": "PR-2210"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "variance_flagged")
        self.assertEqual(response.data["variance"], Decimal("-0.080"))

        response = self.client.post(
            reverse("logistics-incident-resolve", args=[incident.id]),
            {"resolution": "Variance accepted after joint review.", "status": "resolved"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "resolved")

    def test_accepting_transport_request_creates_movement_and_decline_records_reason(self):
        request_item = TransportRequest.objects.create(
            company=self.company,
            rfq_id="RFQ-9001",
            transaction_id="TXN-9001",
            movement_type="mineral haulage",
            requester="Marketplace",
            miner="Jos Tin Collective",
            buyer="Baltic Ore AG",
            mineral="Cassiterite",
            quantity="24.000",
            origin="Mine JS-011",
            destination="Warehouse ABJ-2",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("logistics-transport-request-accept", args=[request_item.id]))

        self.assertEqual(response.status_code, 200)
        request_item.refresh_from_db()
        self.assertEqual(request_item.status, "accepted")
        movement = request_item.movements.get()
        self.assertTrue(movement.reference.startswith("MOV-"))
        self.assertEqual(movement.origin, request_item.origin)
        self.assertEqual(movement.status, "scheduled")

        second = TransportRequest.objects.create(
            company=self.company,
            movement_type="sample",
            requester="Quality",
            quantity="8.400",
            quantity_unit="kg",
            origin="Mine KD-019",
            destination="ABC Laboratory",
        )
        response = self.client.post(
            reverse("logistics-transport-request-decline", args=[second.id]),
            {"reason": "No vehicle available."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        second.refresh_from_db()
        self.assertEqual(second.status, "cancelled")
        self.assertEqual(second.blocked_reason, "No vehicle available.")

    def test_operations_references_are_server_generated_and_company_cannot_be_moved(self):
        other_company = LogisticsCompany.objects.create(
            organisation=self.other_org,
            contact_name="Other Owner",
            contact_email="ops@other.example",
            contact_phone="+2348111111111",
            services=["general freight"],
        )

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-transport-request-list"),
            {
                "company": str(self.company.id),
                "reference": "TR-BROWSER",
                "movement_type": "sample",
                "requester": "Quality",
                "quantity": "1.000",
                "origin": "Mine",
                "destination": "Lab",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.data["reference"], "TR-BROWSER")
        response = self.client.patch(
            reverse("logistics-transport-request-detail", args=[response.data["id"]]),
            {"company": str(other_company.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("company", response.data["error"]["details"])

    def test_assignment_rejects_expired_assets_and_restricted_scope(self):
        expired_vehicle = self.make_vehicle(
            registration="LG-999",
            vin="VIN00000000000999",
            insurance_expiry=timezone.localdate() - timedelta(days=1),
        )
        movement = self.make_movement(movement_type="mineral haulage")

        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("logistics-movement-assign", args=[movement.id]),
            {"vehicle": str(expired_vehicle.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "vehicle_insurance_expired")

        ScopeRestriction.objects.create(company=self.company, service_scope="mineral haulage", reason="Pending renewal")
        vehicle, driver = self.make_ops_vehicle_driver()
        response = self.client.post(
            reverse("logistics-movement-assign", args=[movement.id]),
            {"vehicle": str(vehicle.id), "driver": str(driver.id)},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "service_scope_restricted")

    def test_movement_arrival_delivery_and_completion_are_separate(self):
        movement = self.make_movement(status="scheduled")
        self.client.force_authenticate(self.owner)

        invalid = self.client.post(
            reverse("logistics-movement-set-status", args=[movement.id]),
            {"status": "delivered"},
            format="json",
        )
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(invalid.data["error"]["code"], "invalid_movement_status_transition")

        self.assertEqual(
            self.client.post(reverse("logistics-movement-set-status", args=[movement.id]), {"status": "assigned"}, format="json").status_code,
            200,
        )
        eta = timezone.now() + timedelta(hours=2)
        response = self.client.post(
            reverse("logistics-movement-set-status", args=[movement.id]),
            {"status": "in_transit", "eta_at": eta.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data["eta_at"])

        arrived = self.client.post(reverse("logistics-movement-set-status", args=[movement.id]), {"status": "arrived"}, format="json")
        self.assertEqual(arrived.status_code, 200)
        self.assertEqual(arrived.data["status"], "arrived")
        delivery = movement.deliveries.get()
        self.assertEqual(delivery.status, "arrived")
        self.assertIsNotNone(delivery.arrived_at)
        self.assertIsNone(delivery.received_quantity)
        self.assertIsNone(delivery.custody_transferred_at)

        delivered = self.client.post(reverse("logistics-movement-set-status", args=[movement.id]), {"status": "delivered"}, format="json")
        self.assertEqual(delivered.status_code, 200)
        self.assertEqual(delivered.data["status"], "delivered")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "arrived")
        self.assertIsNone(delivery.received_quantity)
        self.assertIsNone(delivery.custody_transferred_at)

    def test_legacy_operations_status_values_are_normalised(self):
        from importlib import import_module

        migration = import_module("logistics.migrations.0004_operationsdocument_file_and_more")
        movement = self.make_movement(status="scheduled")
        incident = Incident.objects.create(
            company=self.company,
            movement=movement,
            incident_type="Delay",
            severity="medium",
            status="Resolved",
            occurred_at=timezone.now(),
            description="Legacy status.",
        )
        finding = ComplianceFinding.objects.create(
            company=self.company,
            area="Vehicle",
            detail="Legacy status.",
            status="Corrective Action Submitted",
        )
        delivery = Delivery.objects.create(
            company=self.company,
            movement=movement,
            destination_type="Warehouse",
            destination="ABJ-2",
            expected_quantity="10.000",
            status="Delivered",
        )
        payment = LogisticsPayment.objects.create(
            company=self.company,
            status="Partially Paid",
        )
        document = OperationsDocument.objects.create(
            company=self.company,
            name="Permit",
            document_type="Permit",
            verification_status="Action Required",
            compliance_status="Under Review",
        )

        migration.normalise_status_values(apps, None)

        for obj, expected in [
            (incident, "resolved"),
            (finding, "corrective_action_submitted"),
            (delivery, "completed"),
            (payment, "part_paid"),
        ]:
            obj.refresh_from_db()
            self.assertEqual(obj.status, expected)
            obj.full_clean()
        document.refresh_from_db()
        self.assertEqual(document.verification_status, "action_required")
        self.assertEqual(document.compliance_status, "under_review")
        document.full_clean()
