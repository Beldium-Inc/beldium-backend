from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from processing import checklist
from processing.reports import ReportKind
from processing.models import (
    ApplicationDecision,
    ComplianceReport,
    ApplicationSection,
    ApplicationStage,
    EnvironmentalAlert,
    Facility,
    Incident,
    Inspection,
    NonConformity,
    ProcessingApplication,
    ProcessingDocument,
    ProcessingType,
    Processor,
    ProcessorStatus,
    ReviewState,
    RiskCause,
    SectionKey,
    TraceabilityRun,
)


def make_user(email, **extra):
    return User.objects.create_user(email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), **extra)


def make_org(name, org_type):
    return Organisation.objects.create(name=name, organisation_type=org_type, verification_status="verified")


class ProcessingTestCase(APITestCase):
    """Three audiences over one register, which is what every test needs."""

    def setUp(self):
        self.desk_org = make_org("Beldium Compliance Desk", OrganisationType.COMPLIANCE_PARTNER)
        self.regulator_org = make_org("Minerals Oversight Directorate", OrganisationType.REGULATOR)
        self.processor_org = make_org("Ilesa Mineral Processing Ltd", OrganisationType.MINING_COMPANY)
        self.other_org = make_org("Jos Tin Sorting Enterprises", OrganisationType.MINING_COMPANY)

        self.operator = make_user("operator@beldium.test")
        OrganisationMembership.objects.create(organisation=self.desk_org, user=self.operator, role=MembershipRole.REVIEWER)

        self.analyst = make_user("analyst@beldium.test")
        OrganisationMembership.objects.create(organisation=self.desk_org, user=self.analyst, role=MembershipRole.ANALYST)

        self.regulator = make_user("regulator@beldium.test")
        OrganisationMembership.objects.create(organisation=self.regulator_org, user=self.regulator, role=MembershipRole.READ_ONLY)

        self.applicant = make_user("applicant@ilesa.test")
        OrganisationMembership.objects.create(organisation=self.processor_org, user=self.applicant, role=MembershipRole.OWNER)

        self.outsider = make_user("outsider@jos.test")
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)

        self.processor = Processor.objects.create(
            organisation=self.processor_org,
            name="Ilesa Mineral Processing Ltd",
            rc_number="RC 1428907",
            processing_type=ProcessingType.CHEMICAL_REFINING,
            state="Osun",
            region="South West",
            status=ProcessorStatus.UNDER_REVIEW,
            compliance_score=58,
        )
        self.facility = Facility.objects.create(processor=self.processor, name="Ilesa Refining Plant A", state="Osun")

    def application(self, **overrides):
        defaults = {
            "processor": self.processor,
            "organisation": self.processor_org,
            "created_by": self.applicant,
            "company": "Ilesa Mineral Processing Ltd",
            "processing_type": ProcessingType.CHEMICAL_REFINING,
            "state": "Osun",
            "facility_name": "Ilesa Refining Plant A",
            "stage": ApplicationStage.NEW,
        }
        application = ProcessingApplication.objects.create(**{**defaults, **overrides})
        ApplicationSection.objects.bulk_create(
            [ApplicationSection(application=application, key=key) for key, _ in SectionKey.choices]
        )
        return application

    def complete(self, application, review_state=None):
        """Answer every required prompt and supply every required document.

        Completeness is defined by the checklist, so a test that wants a
        submittable application has to satisfy the checklist rather than write
        one arbitrary field.
        """
        for section in application.sections.all():
            section.fields = [
                {"label": prompt, "value": "Declared"}
                for prompt in checklist.required_prompts(section.key)
            ]
            if review_state:
                section.review_state = review_state
            section.save()
        for section, name, _issuer, _expires in checklist.documents_for(application.processing_type):
            ProcessingDocument.objects.update_or_create(
                application=application,
                section=section,
                name=name,
                defaults={"processor": application.processor, "file": "processing/documents/test.pdf"},
            )
        return application


class AudienceTests(ProcessingTestCase):
    def test_capabilities_reflect_membership_not_a_client_claim(self):
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("processing-capabilities"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["audience"], "operator")
        self.assertTrue(response.data["can_decide"])

        self.client.force_authenticate(self.regulator)
        response = self.client.get(reverse("processing-capabilities"))
        self.assertEqual(response.data["audience"], "regulator")
        self.assertFalse(response.data["can_decide"])

        self.client.force_authenticate(self.applicant)
        response = self.client.get(reverse("processing-capabilities"))
        self.assertEqual(response.data["audience"], "processor")
        self.assertFalse(response.data["can_decide"])

    def test_user_without_any_membership_is_refused(self):
        self.client.force_authenticate(make_user("nobody@example.test"))
        self.assertEqual(self.client.get(reverse("processor-list")).status_code, 403)

    def test_a_processor_sees_only_its_own_applications(self):
        self.application()
        self.client.force_authenticate(self.outsider)
        response = self.client.get(reverse("processing-application-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

        self.client.force_authenticate(self.applicant)
        self.assertEqual(self.client.get(reverse("processing-application-list")).data["count"], 1)

    def test_regulator_reads_the_whole_register_but_cannot_write(self):
        self.application()
        self.client.force_authenticate(self.regulator)
        self.assertEqual(self.client.get(reverse("processing-application-list")).data["count"], 1)
        response = self.client.post(
            reverse("processor-list"),
            {"name": "New Processor Ltd", "processing_type": ProcessingType.SMELTING},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_desk_membership_without_a_decision_role_cannot_review(self):
        application = self.complete(self.application())
        self.client.force_authenticate(self.analyst)
        # Reading the register is fine; acting on it is not.
        self.assertEqual(self.client.get(reverse("processing-application-list")).data["count"], 1)
        response = self.client.post(
            reverse("processing-application-review-section", args=[application.id, SectionKey.CORPORATE]),
            {"review_state": "verified"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


class ApplicationLifecycleTests(ProcessingTestCase):
    def test_creating_an_application_lays_down_all_ten_sections(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-application-list"),
            {
                "company": "Ilesa Mineral Processing Ltd",
                "processing_type": ProcessingType.CHEMICAL_REFINING,
                "state": "Osun",
                "organisation": str(self.processor_org.id),
                "processor": str(self.processor.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        application = ProcessingApplication.objects.get(id=response.data["id"])
        self.assertEqual(application.sections.count(), len(SectionKey.choices))
        self.assertTrue(application.reference.startswith("BPC-APP-"))

    def test_submission_is_refused_until_every_section_is_complete(self):
        application = self.application()
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("processing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "application_incomplete")

        self.complete(application)
        response = self.client.post(reverse("processing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stage"], ApplicationStage.IN_REVIEW)
        self.assertEqual(response.data["completeness"], 100)

    def test_a_section_missing_a_required_document_is_not_complete(self):
        application = self.complete(self.application())
        application.documents.filter(section=SectionKey.WASTE).update(file="")
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("processing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        details = response.data["error"]["details"]
        self.assertEqual(details["completeness"], 90)
        gap = next(row for row in details["outstanding"] if row["section"] == SectionKey.WASTE)
        self.assertIn("Waste Handler Contract", gap["missing_documents"])

    def test_a_section_missing_a_required_answer_is_not_complete(self):
        application = self.complete(self.application())
        section = application.sections.get(key=SectionKey.QUALITY)
        section.fields = [field for field in section.fields if field["label"] != "Assay method"]
        section.save()
        self.client.force_authenticate(self.applicant)
        response = self.client.post(reverse("processing-application-submit", args=[application.id]))
        self.assertEqual(response.status_code, 409)
        gap = next(
            row for row in response.data["error"]["details"]["outstanding"] if row["section"] == SectionKey.QUALITY
        )
        self.assertEqual(gap["missing_prompts"], ["Assay method"])

    def test_a_refinery_must_evidence_more_than_a_crushing_plant(self):
        """The checklist is per process class, so the gap list differs by type."""
        refinery = set(checklist.required_documents_by_section(ProcessingType.CHEMICAL_REFINING)[SectionKey.REGULATORY])
        crusher = set(checklist.required_documents_by_section(ProcessingType.CRUSHING_MILLING)[SectionKey.REGULATORY])
        self.assertIn("NESREA effluent discharge permit", refinery)
        self.assertNotIn("NESREA effluent discharge permit", crusher)

    def test_editing_a_section_returns_it_to_the_review_queue(self):
        application = self.complete(self.application())
        section = application.sections.get(key=SectionKey.CORPORATE)
        section.review_state = ReviewState.VERIFIED
        section.reviewed_by = self.operator
        section.save()

        self.client.force_authenticate(self.applicant)
        response = self.client.patch(
            reverse("processing-application-section", args=[application.id, SectionKey.CORPORATE]),
            {"fields": [{"label": "Directors declared", "value": "5"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["review_state"], ReviewState.PENDING)
        section.refresh_from_db()
        self.assertIsNone(section.reviewed_by)

    def test_requesting_information_hands_the_application_back(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-review-section", args=[application.id, SectionKey.ENVIRONMENTAL]),
            {"review_state": "info_requested", "note": "Bund capacity calculation missing."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.stage, ApplicationStage.AWAITING_INFO)

    def test_an_applicant_cannot_edit_once_the_desk_has_the_application(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.applicant)
        response = self.client.patch(
            reverse("processing-application-section", args=[application.id, SectionKey.CORPORATE]),
            {"fields": [{"label": "Directors declared", "value": "9"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "application_locked")

    def test_approval_requires_every_section_verified(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-decide", args=[application.id]),
            {"decision": ApplicationDecision.APPROVED},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "sections_not_verified")

    def test_approval_requires_open_findings_to_be_closed_first(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW), ReviewState.VERIFIED)
        NonConformity.objects.create(
            application=application,
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MAJOR,
            title="Slag outside containment",
            due_on=timezone.localdate() + timedelta(days=10),
        )
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-decide", args=[application.id]),
            {"decision": ApplicationDecision.APPROVED},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "non_conformities_open")

    def test_approval_promotes_the_processor_and_scores_it_from_the_risk_causes(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW), ReviewState.VERIFIED)
        RiskCause.objects.create(application=application, cause="Chemical process class", weight=22)
        RiskCause.objects.create(application=application, cause="Expiring registration", weight=16)

        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-decide", args=[application.id]),
            {"decision": ApplicationDecision.APPROVED, "note": "Approved subject to surveillance."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stage"], ApplicationStage.DECIDED)
        self.assertEqual(response.data["risk_score"], 38)
        self.assertEqual(response.data["risk_band"], "medium")
        self.processor.refresh_from_db()
        self.assertEqual(self.processor.status, ProcessorStatus.APPROVED)
        self.assertEqual(self.processor.compliance_score, 62)

    def test_more_information_is_not_a_final_decision(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-decide", args=[application.id]),
            {"decision": ApplicationDecision.MORE_INFO, "note": "Send the bund calculation."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        application.refresh_from_db()
        self.assertEqual(application.stage, ApplicationStage.AWAITING_INFO)
        self.assertEqual(application.decision, "")

    def test_a_decided_application_cannot_be_decided_again(self):
        application = self.complete(self.application(stage=ApplicationStage.DECIDED, decision=ApplicationDecision.REJECTED))
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-decide", args=[application.id]),
            {"decision": ApplicationDecision.APPROVED},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "application_decided")

    def test_requesting_an_inspection_moves_the_application_and_records_one(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-request-inspection", args=[application.id]),
            {"inspection_type": Inspection.Type.PRE_APPROVAL},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], Inspection.Status.REQUESTED)
        self.assertEqual(response.data["inspector_display"], "Unassigned")
        application.refresh_from_db()
        self.assertEqual(application.stage, ApplicationStage.INSPECTION)

    def test_an_inspection_cannot_be_scheduled_in_the_past(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-request-inspection", args=[application.id]),
            {"scheduled_for": (timezone.localdate() - timedelta(days=1)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_activity_records_what_the_desk_did(self):
        application = self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        self.client.force_authenticate(self.operator)
        self.client.post(
            reverse("processing-application-review-section", args=[application.id, SectionKey.CORPORATE]),
            {"review_state": "verified"},
            format="json",
        )
        response = self.client.get(reverse("processing-application-activity", args=[application.id]))
        self.assertEqual(response.status_code, 200)
        entry = response.data["results"][0]
        self.assertEqual(entry["action"], "Section reviewed")
        self.assertIn("Corporate", entry["target"])


class NonConformityTests(ProcessingTestCase):
    def setUp(self):
        super().setUp()
        self.application_row = self.application(stage=ApplicationStage.IN_REVIEW)
        self.finding = NonConformity.objects.create(
            application=self.application_row,
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MAJOR,
            title="Slag storage exceeds containment",
            due_on=timezone.localdate() + timedelta(days=14),
        )

    def test_only_the_desk_can_raise_a_finding(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-non-conformity-list"),
            {
                "application": str(self.application_row.id),
                "section": SectionKey.WASTE,
                "severity": NonConformity.Severity.MINOR,
                "title": "Self-raised",
                "due_on": (timezone.localdate() + timedelta(days=10)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_a_deadline_cannot_be_set_in_the_past(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-non-conformity-list"),
            {
                "application": str(self.application_row.id),
                "section": SectionKey.WASTE,
                "severity": NonConformity.Severity.MINOR,
                "title": "Backdated",
                "due_on": (timezone.localdate() - timedelta(days=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_the_processor_submits_evidence_and_the_desk_closes_it(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-non-conformity-evidence", args=[self.finding.id]),
            {"name": "Relocation photo set", "note": "Stockpile moved to the lined cell."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.finding.refresh_from_db()
        self.assertEqual(self.finding.status, NonConformity.Status.EVIDENCE_SUBMITTED)

        self.client.force_authenticate(self.applicant)
        refused = self.client.post(
            reverse("processing-non-conformity-close", args=[self.finding.id]), {"accept": True}, format="json"
        )
        self.assertEqual(refused.status_code, 403)

        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-non-conformity-close", args=[self.finding.id]),
            {"accept": True, "note": "Manifest verified."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], NonConformity.Status.CLOSED)

    def test_closing_without_evidence_is_refused(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-non-conformity-close", args=[self.finding.id]), {"accept": True}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "evidence_required")

    def test_rejecting_evidence_reopens_the_finding(self):
        self.finding.status = NonConformity.Status.EVIDENCE_SUBMITTED
        self.finding.save()
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-non-conformity-close", args=[self.finding.id]),
            {"accept": False, "note": "Manifest does not cover the removed tonnage."},
            format="json",
        )
        self.assertEqual(response.data["status"], NonConformity.Status.OPEN)

    def test_an_overdue_finding_is_reported_as_overdue(self):
        self.finding.due_on = timezone.localdate() - timedelta(days=2)
        self.finding.save()
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("processing-non-conformity-detail", args=[self.finding.id]))
        self.assertTrue(response.data["is_overdue"])


class RegisterTests(ProcessingTestCase):
    def test_document_validity_is_derived_from_the_expiry_date(self):
        application = self.application()
        today = timezone.localdate()
        cases = {
            "valid": today + timedelta(days=365),
            "expiring": today + timedelta(days=30),
            "expired": today - timedelta(days=1),
        }
        for expected, expires_on in cases.items():
            document = ProcessingDocument.objects.create(
                application=application,
                section=SectionKey.REGULATORY,
                name=f"Permit {expected}",
                expires_on=expires_on,
                file="processing/documents/test.pdf",
            )
            self.assertEqual(document.status, expected)

        without_file = ProcessingDocument.objects.create(
            application=application, section=SectionKey.REGULATORY, name="Not yet supplied"
        )
        self.assertEqual(without_file.status, "missing")

    def test_the_expiry_watchlist_returns_lapsed_and_lapsing_documents(self):
        application = self.application()
        today = timezone.localdate()
        for name, offset in [("Lapsed", -5), ("Lapsing", 30), ("Comfortable", 400)]:
            ProcessingDocument.objects.create(
                application=application,
                section=SectionKey.REGULATORY,
                name=name,
                expires_on=today + timedelta(days=offset),
                file="processing/documents/test.pdf",
            )
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("processing-document-expiring"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.data], ["Lapsed", "Lapsing"])

    def test_a_run_cannot_yield_more_than_it_consumed(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-run-list"),
            {
                "processor": str(self.processor.id),
                "input_batch": "BLD-IN-1",
                "input_mass_kg": 1000,
                "output_batch": "BLD-OUT-1",
                "output_mass_kg": 1200,
                "started_at": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("output_mass_kg", response.data["error"]["details"])

    def test_run_yield_is_computed_from_the_masses(self):
        run = TraceabilityRun.objects.create(
            processor=self.processor,
            input_batch="BLD-IN-2",
            input_mass_kg=24_600,
            output_batch="BLD-OUT-2",
            output_mass_kg=8_200,
            started_at=timezone.now(),
        )
        self.assertEqual(run.yield_percent, 33.3)

    def test_an_alert_moves_open_to_acknowledged_to_resolved_once(self):
        alert = EnvironmentalAlert.objects.create(
            processor=self.processor,
            facility=self.facility,
            facility_name=self.facility.name,
            parameter="Effluent pH",
            reading="5.4",
            threshold="6.0 - 9.0",
            severity=EnvironmentalAlert.Severity.WARNING,
        )
        self.client.force_authenticate(self.operator)
        url = reverse("processing-environmental-alert-set-status", args=[alert.id])
        self.assertEqual(self.client.post(url, {"status": "acknowledged"}, format="json").status_code, 200)
        self.assertEqual(self.client.post(url, {"status": "resolved"}, format="json").status_code, 200)
        repeat = self.client.post(url, {"status": "resolved"}, format="json")
        self.assertEqual(repeat.status_code, 409)
        self.assertEqual(repeat.data["error"]["code"], "alert_resolved")

    def test_a_processor_may_report_its_own_incident(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-incident-list"),
            {
                "processor": str(self.processor.id),
                "facility_name": self.facility.name,
                "incident_type": "Chemical spill (contained)",
                "severity": Incident.Severity.MODERATE,
                "summary": "12 litres released within the bunded area.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data["reference"].startswith("BPC-INC-"))

    def test_the_processor_register_counts_facilities_and_open_findings(self):
        NonConformity.objects.create(
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MINOR,
            title="Open finding",
            due_on=timezone.localdate() + timedelta(days=5),
        )
        NonConformity.objects.create(
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MINOR,
            title="Closed finding",
            due_on=timezone.localdate() + timedelta(days=5),
            status=NonConformity.Status.CLOSED,
        )
        self.client.force_authenticate(self.regulator)
        row = self.client.get(reverse("processor-list")).data["results"][0]
        self.assertEqual(row["facilities_count"], 1)
        self.assertEqual(row["open_non_conformities"], 1)


class DashboardTests(ProcessingTestCase):
    def test_the_dashboard_totals_reflect_the_register(self):
        self.complete(self.application(stage=ApplicationStage.IN_REVIEW))
        NonConformity.objects.create(
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MAJOR,
            title="Overdue finding",
            due_on=timezone.localdate() - timedelta(days=3),
        )
        EnvironmentalAlert.objects.create(
            processor=self.processor,
            parameter="PM10",
            reading="128",
            threshold="100",
            severity=EnvironmentalAlert.Severity.CRITICAL,
        )
        self.client.force_authenticate(self.operator)
        response = self.client.get(reverse("processing-dashboard"))
        self.assertEqual(response.status_code, 200)
        totals = response.data["totals"]
        self.assertEqual(totals["applications_in_review"], 1)
        self.assertEqual(totals["open_non_conformities"], 1)
        self.assertEqual(totals["overdue_non_conformities"], 1)
        self.assertEqual(totals["open_environmental_alerts"], 1)
        self.assertEqual(len(response.data["kpi_trend"]), 6)
        self.assertEqual(response.data["regional_compliance"][0]["region"], "South West")

    def test_a_processor_sees_only_its_own_totals(self):
        self.application()
        self.client.force_authenticate(self.outsider)
        response = self.client.get(reverse("processing-dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["totals"]["applications"], 0)
        self.assertEqual(response.data["totals"]["processors"], 0)

    def test_the_audit_feed_is_closed_to_a_single_processor(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.get(reverse("processing-audit-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)


class DocumentReviewTests(ProcessingTestCase):
    def setUp(self):
        super().setUp()
        self.application_row = self.application(stage=ApplicationStage.IN_REVIEW)
        self.document = ProcessingDocument.objects.create(
            application=self.application_row,
            processor=self.processor,
            section=SectionKey.REGULATORY,
            name="NESREA Facility Registration",
            file="processing/documents/test.pdf",
        )

    def test_the_desk_accepts_a_supplied_document(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-document-review", args=[self.document.id]),
            {"review_state": "verified", "note": "Registration confirmed against the NESREA register."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["review_state"], ReviewState.VERIFIED)
        self.document.refresh_from_db()
        self.assertEqual(self.document.reviewed_by, self.operator)

    def test_a_document_with_no_file_cannot_be_reviewed(self):
        empty = ProcessingDocument.objects.create(
            application=self.application_row, section=SectionKey.WASTE, name="Waste handler contract"
        )
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-document-review", args=[empty.id]), {"review_state": "verified"}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "document_not_supplied")

    def test_an_applicant_cannot_review_its_own_evidence(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-document-review", args=[self.document.id]), {"review_state": "verified"}, format="json"
        )
        self.assertEqual(response.status_code, 403)


class CrossCompanyWriteTests(ProcessingTestCase):
    """A processor writes its own records, never another company's."""

    def setUp(self):
        super().setUp()
        self.other_processor = Processor.objects.create(
            organisation=self.other_org,
            name="Jos Tin Sorting Enterprises",
            processing_type=ProcessingType.SORTING_BALING,
            state="Plateau",
        )

    def test_a_processor_cannot_report_an_incident_at_another_company(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-incident-list"),
            {
                "processor": str(self.other_processor.id),
                "incident_type": "Fabricated event",
                "severity": Incident.Severity.SEVERE,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_a_processor_cannot_record_a_run_for_another_company(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-run-list"),
            {
                "processor": str(self.other_processor.id),
                "input_batch": "BLD-IN-9",
                "input_mass_kg": 1000,
                "output_batch": "BLD-OUT-9",
                "output_mass_kg": 900,
                "started_at": timezone.now().isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_a_processor_cannot_file_an_application_for_another_organisation(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-application-list"),
            {
                "company": "Jos Tin Sorting Enterprises",
                "processing_type": ProcessingType.SORTING_BALING,
                "organisation": str(self.other_org.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_the_desk_may_write_against_any_processor(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-incident-list"),
            {
                "processor": str(self.other_processor.id),
                "incident_type": "Equipment failure",
                "severity": Incident.Severity.MODERATE,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_a_processor_files_its_own_application(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-application-list"),
            {
                "company": "Ilesa Mineral Processing Ltd",
                "processing_type": ProcessingType.CHEMICAL_REFINING,
                "organisation": str(self.processor_org.id),
                "processor": str(self.processor.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)


class ApplicantFlowTests(ProcessingTestCase):
    """The path a processor drives: start, fill in, evidence, submit, respond."""

    def test_the_checklist_is_served_per_process_class(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.get(reverse("processing-checklist"), {"processing_type": "chemical_refining"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["sections"]), 10)
        regulatory = next(s for s in response.data["sections"] if s["key"] == SectionKey.REGULATORY)
        self.assertIn("NESREA effluent discharge permit", regulatory["required_documents"])
        self.assertIn("Issuing authority", regulatory["required_prompts"])

    def test_an_unknown_process_class_is_refused(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.get(reverse("processing-checklist"), {"processing_type": "alchemy"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "unknown_processing_type")

    def test_an_applicant_answers_a_section(self):
        application = self.application()
        self.client.force_authenticate(self.applicant)
        response = self.client.patch(
            reverse("processing-application-section", args=[application.id, SectionKey.CORPORATE]),
            {
                "fields": [
                    {"label": "Registered name", "value": "Ilesa Mineral Processing Ltd"},
                    {"label": "Beneficial ownership disclosed", "value": "Yes", "flag": "ok"},
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["fields"]), 2)

    def test_a_malformed_field_is_refused(self):
        application = self.application()
        self.client.force_authenticate(self.applicant)
        response = self.client.patch(
            reverse("processing-application-section", args=[application.id, SectionKey.CORPORATE]),
            {"fields": [{"value": "no label"}]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_re_uploading_a_document_replaces_it_and_clears_the_verdict(self):
        application = self.application()
        self.client.force_authenticate(self.applicant)
        payload = {
            "section": SectionKey.CORPORATE,
            "name": "CAC Certificate of Incorporation",
            "reference": "RC 1428907",
            "issuer": "CAC",
        }
        first = self.client.post(
            reverse("processing-application-documents", args=[application.id]), payload, format="multipart"
        )
        self.assertEqual(first.status_code, 201)

        # The desk accepts it, then the applicant replaces the file.
        document = application.documents.get(name=payload["name"])
        document.file = "processing/documents/test.pdf"
        document.review_state = ReviewState.VERIFIED
        document.save()

        second = self.client.post(
            reverse("processing-application-documents", args=[application.id]), payload, format="multipart"
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(application.documents.filter(name=payload["name"]).count(), 1)
        self.assertEqual(second.data["review_state"], ReviewState.PENDING)

    def test_the_gap_list_names_what_is_still_outstanding(self):
        application = self.application()
        self.client.force_authenticate(self.applicant)
        response = self.client.get(reverse("processing-application-detail", args=[application.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["completeness"], 0)
        self.assertEqual(len(response.data["outstanding"]), 10)
        corporate = next(row for row in response.data["outstanding"] if row["section"] == SectionKey.CORPORATE)
        self.assertIn("Registered name", corporate["missing_prompts"])
        self.assertIn("CAC Certificate of Incorporation", corporate["missing_documents"])

    def test_a_full_applicant_round_trip(self):
        """Start, complete, submit, get sent back, fix, resubmit."""
        self.client.force_authenticate(self.applicant)
        created = self.client.post(
            reverse("processing-application-list"),
            {
                "company": "Ilesa Mineral Processing Ltd",
                "processing_type": ProcessingType.CHEMICAL_REFINING,
                "state": "Osun",
                "organisation": str(self.processor_org.id),
                "processor": str(self.processor.id),
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        application = ProcessingApplication.objects.get(id=created.data["id"])

        self.assertEqual(
            self.client.post(reverse("processing-application-submit", args=[application.id])).status_code, 409
        )

        self.complete(application)
        submitted = self.client.post(reverse("processing-application-submit", args=[application.id]))
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.data["stage"], ApplicationStage.IN_REVIEW)
        self.assertEqual(submitted.data["outstanding"], [])

        # The desk wants more on one section.
        self.client.force_authenticate(self.operator)
        self.client.post(
            reverse("processing-application-review-section", args=[application.id, SectionKey.WASTE]),
            {"review_state": "info_requested", "note": "Name the licensed handler."},
            format="json",
        )
        application.refresh_from_db()
        self.assertEqual(application.stage, ApplicationStage.AWAITING_INFO)

        # The applicant can edit again, and resubmitting returns it to the desk.
        self.client.force_authenticate(self.applicant)
        answered = self.client.patch(
            reverse("processing-application-section", args=[application.id, SectionKey.WASTE]),
            {
                "fields": [
                    {"label": prompt, "value": "Declared"}
                    for prompt in checklist.required_prompts(SectionKey.WASTE)
                ]
            },
            format="json",
        )
        self.assertEqual(answered.status_code, 200)
        resubmitted = self.client.post(reverse("processing-application-submit", args=[application.id]))
        self.assertEqual(resubmitted.status_code, 200)
        self.assertEqual(resubmitted.data["stage"], ApplicationStage.IN_REVIEW)

    def test_an_applicant_submits_corrective_action_evidence(self):
        application = self.application(stage=ApplicationStage.AWAITING_INFO)
        finding = NonConformity.objects.create(
            application=application,
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MAJOR,
            title="Slag outside containment",
            due_on=timezone.localdate() + timedelta(days=10),
        )
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-non-conformity-evidence", args=[finding.id]),
            {"name": "Relocation photo set", "note": "Moved to the lined cell."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["submitted_by_name"], self.applicant.email)
        finding.refresh_from_db()
        self.assertEqual(finding.status, NonConformity.Status.EVIDENCE_SUBMITTED)


class FirstApplicationTests(ProcessingTestCase):
    """A company joining the register has no processor record yet."""

    def test_a_first_time_applicant_files_without_naming_anything(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-application-list"),
            {"company": "Ilesa Thermal Recovery Ltd", "processing_type": ProcessingType.SMELTING},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        application = ProcessingApplication.objects.get(id=response.data["id"])
        # Attributed to their own organisation rather than left unowned.
        self.assertEqual(application.organisation_id, self.processor_org.id)
        self.assertIsNone(application.processor_id)
        self.assertEqual(application.sections.count(), len(SectionKey.choices))

    def test_naming_another_organisation_is_still_refused(self):
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-application-list"),
            {
                "company": "Someone else",
                "processing_type": ProcessingType.SMELTING,
                "organisation": str(self.other_org.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_an_applicant_in_two_organisations_must_say_which(self):
        second = make_org("Ilesa Holdings Ltd", OrganisationType.MINING_COMPANY)
        OrganisationMembership.objects.create(
            organisation=second, user=self.applicant, role=MembershipRole.OWNER
        )
        self.client.force_authenticate(self.applicant)
        response = self.client.post(
            reverse("processing-application-list"),
            {"company": "Ambiguous Ltd", "processing_type": ProcessingType.SMELTING},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "organisation_required")

    def test_the_first_application_is_scoped_to_its_filer(self):
        self.client.force_authenticate(self.applicant)
        self.client.post(
            reverse("processing-application-list"),
            {"company": "Ilesa Thermal Recovery Ltd", "processing_type": ProcessingType.SMELTING},
            format="json",
        )
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(reverse("processing-application-list")).data["count"], 0)


class RiskCauseTests(ProcessingTestCase):
    """The score is the sum of its causes, so the desk must be able to edit them."""

    def setUp(self):
        super().setUp()
        self.application_row = self.application(stage=ApplicationStage.IN_REVIEW)
        self.cause = RiskCause.objects.create(
            application=self.application_row, cause="Expiring registration", weight=16
        )

    def test_the_desk_adds_a_cause_and_the_score_follows(self):
        self.client.force_authenticate(self.operator)
        response = self.client.post(
            reverse("processing-application-risk-causes", args=[self.application_row.id]),
            {"cause": "Chemical process class", "weight": 22, "detail": "Acid leaching on site."},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        detail = self.client.get(
            reverse("processing-application-detail", args=[self.application_row.id])
        )
        self.assertEqual(detail.data["risk_score"], 38)
        self.assertEqual(detail.data["risk_band"], "medium")

    def test_the_desk_withdraws_a_cause(self):
        """This route was unreachable while DELETE was off the viewset."""
        self.client.force_authenticate(self.operator)
        response = self.client.delete(
            reverse(
                "processing-application-risk-cause-detail",
                args=[self.application_row.id, self.cause.id],
            )
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.application_row.risk_causes.count(), 0)

    def test_an_applicant_cannot_edit_its_own_risk_causes(self):
        self.client.force_authenticate(self.applicant)
        self.assertEqual(
            self.client.delete(
                reverse(
                    "processing-application-risk-cause-detail",
                    args=[self.application_row.id, self.cause.id],
                )
            ).status_code,
            403,
        )

    def test_the_application_itself_cannot_be_deleted(self):
        self.client.force_authenticate(self.operator)
        response = self.client.delete(
            reverse("processing-application-detail", args=[self.application_row.id])
        )
        self.assertEqual(response.status_code, 405)


class ReportGenerationTests(ProcessingTestCase):
    """Reports are compiled from the register and stored as they were issued."""

    def setUp(self):
        super().setUp()
        self.processor.region = "South West"
        self.processor.save(update_fields=["region"])
        self.application_row = self.application(stage=ApplicationStage.IN_REVIEW)
        NonConformity.objects.create(
            application=self.application_row,
            processor=self.processor,
            section=SectionKey.WASTE,
            severity=NonConformity.Severity.MAJOR,
            title="Slag outside containment",
            due_on=timezone.localdate() + timedelta(days=10),
        )
        EnvironmentalAlert.objects.create(
            processor=self.processor,
            facility_name="Ilesa Refining Plant A",
            parameter="Effluent pH",
            reading="5.4",
            threshold="6.0 - 9.0",
            severity=EnvironmentalAlert.Severity.WARNING,
        )
        Inspection.objects.create(
            processor=self.processor,
            facility_name="Ilesa Refining Plant A",
            scheduled_for=timezone.localdate() + timedelta(days=7),
            status=Inspection.Status.SCHEDULED,
        )

    def _generate(self, **overrides):
        payload = {"kind": "national_compliance", "scope": "All regions", "period": "year_to_date"}
        payload.update(overrides)
        return self.client.post(reverse("processing-report-generate"), payload, format="json")

    def test_every_kind_compiles_a_stored_pdf(self):
        self.client.force_authenticate(self.operator)
        for kind, label in ReportKind.CHOICES:
            with self.subTest(kind=kind):
                response = self._generate(kind=kind)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["title"], label)
                self.assertGreaterEqual(response.data["pages"], 1)
                self.assertIsNotNone(response.data["file_url"])

                report = ComplianceReport.objects.get(id=response.data["id"])
                self.assertTrue(report.file)
                with report.file.open("rb") as stored:
                    self.assertTrue(stored.read(5).startswith(b"%PDF-"))

    def test_the_stored_document_downloads(self):
        self.client.force_authenticate(self.operator)
        created = self._generate()
        response = self.client.get(
            reverse("processing-report-download", args=[created.data["id"]])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF-"))

    def test_the_reference_is_stamped_before_the_document_is_rendered(self):
        """It appears on every page, so it cannot be assigned after the build."""
        self.client.force_authenticate(self.operator)
        report = ComplianceReport.objects.get(id=self._generate().data["id"])
        with report.file.open("rb") as stored:
            self.assertIn(report.reference.encode(), stored.read())

    def test_a_regulator_may_compile_one(self):
        self.client.force_authenticate(self.regulator)
        self.assertEqual(self._generate().status_code, 201)

    def test_a_processor_cannot_compile_a_register_wide_report(self):
        self.client.force_authenticate(self.applicant)
        self.assertEqual(self._generate().status_code, 403)

    def test_an_unknown_region_is_refused(self):
        self.client.force_authenticate(self.operator)
        response = self._generate(scope="Atlantis")
        self.assertEqual(response.status_code, 400)
        self.assertIn("scope", response.data["error"]["details"])

    def test_a_known_region_is_accepted_and_recorded(self):
        self.client.force_authenticate(self.operator)
        response = self._generate(scope="South West")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["scope"], "South West")

    def test_an_unknown_kind_is_refused(self):
        self.client.force_authenticate(self.operator)
        self.assertEqual(self._generate(kind="astrology").status_code, 400)

    def test_the_period_label_describes_the_range(self):
        self.client.force_authenticate(self.operator)
        self.assertEqual(self._generate(period="all_time").data["period_label"], "All time")
        self.assertIn(
            str(timezone.localdate().year), self._generate(period="year_to_date").data["period_label"]
        )

    def test_generation_is_written_to_the_audit_trail(self):
        self.client.force_authenticate(self.operator)
        created = self._generate()
        response = self.client.get(reverse("processing-audit-list"))
        entry = next(
            row for row in response.data["results"] if row["target"] == created.data["reference"]
        )
        self.assertEqual(entry["action"], "Report generated")

    def test_an_empty_register_still_produces_a_readable_report(self):
        """A period with nothing in it must not render a broken document."""
        NonConformity.objects.all().delete()
        EnvironmentalAlert.objects.all().delete()
        Inspection.objects.all().delete()
        self.client.force_authenticate(self.operator)
        for kind, _ in ReportKind.CHOICES:
            with self.subTest(kind=kind):
                self.assertEqual(self._generate(kind=kind, period="last_month").status_code, 201)
