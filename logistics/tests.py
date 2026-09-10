from datetime import timedelta
from tempfile import TemporaryDirectory

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
    MonitoringEvent,
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
