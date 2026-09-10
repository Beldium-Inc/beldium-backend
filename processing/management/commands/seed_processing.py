"""Populate the processing register with a representative demo dataset.

The connected frontend has no data of its own any more, so a fresh database
gives every processing screen an empty state. This fills it with a register
that exercises each path the UI renders: every processing type, every
application stage, overdue and cleared findings, expiring and expired
documents, and runs at three QC verdicts.

Idempotent: it keys on the demo organisation, so re-running refreshes the data
rather than duplicating it.
"""
import random
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from organisations.models import Organisation, OrganisationType
from processing import checklist
from processing.reports import ReportKind, build as build_report
from processing.models import (
    ApplicationDecision,
    ApplicationSection,
    ApplicationStage,
    ComplianceReport,
    EnvironmentalAlert,
    Facility,
    Incident,
    Inspection,
    NonConformity,
    NonConformityEvidence,
    ProcessingApplication,
    ProcessingDocument,
    ProcessingType,
    Processor,
    generate_report_reference,
    ProcessorStatus,
    ReviewState,
    RiskCause,
    SectionKey,
    TraceabilityRun,
)

DEMO_ORG = "Beldium Processing Demo Register"

PROCESSORS = [
    ("Ogun Industrial Minerals Plc", "RC 501992", "40119922-0001", ProcessingType.CRUSHING_MILLING, "Ogun", "Ewekoro", "South West", ProcessorStatus.APPROVED, 92, 3),
    ("Port Harcourt Metal Recovery Ltd", "RC 1720445", "30881204-0001", ProcessingType.SMELTING, "Rivers", "Obio-Akpor", "South South", ProcessorStatus.CONDITIONAL, 64, 1),
    ("Kaduna Aggregate Crushing Co.", "RC 998117", "11940022-0001", ProcessingType.CRUSHING_MILLING, "Kaduna", "Chikun", "North West", ProcessorStatus.UNDER_REVIEW, 71, 2),
    ("Ilesa Mineral Processing Ltd", "RC 1428907", "20418833-0001", ProcessingType.CHEMICAL_REFINING, "Osun", "Ilesa East", "South West", ProcessorStatus.UNDER_REVIEW, 58, 1),
    ("Enugu Coal Preparation Ltd", "RC 660214", "50231144-0001", ProcessingType.CRUSHING_MILLING, "Enugu", "Udi", "South East", ProcessorStatus.SUSPENDED, 41, 1),
    ("Jos Tin Sorting Enterprises", "RC 774318", "10022778-0001", ProcessingType.SORTING_BALING, "Plateau", "Jos South", "North Central", ProcessorStatus.APPROVED, 76, 1),
]

FACILITY_NAMES = {
    "Ogun Industrial Minerals Plc": ["Ewekoro Milling Complex", "Papalanto Screening Yard", "Itori Aggregate Plant"],
    "Port Harcourt Metal Recovery Ltd": ["Trans-Amadi Recovery Works"],
    "Kaduna Aggregate Crushing Co.": ["Chikun Quarry Plant", "Rigachikun Crushing Line"],
    "Ilesa Mineral Processing Ltd": ["Ilesa Refining Plant A"],
    "Enugu Coal Preparation Ltd": ["Udi Coal Wash Plant"],
    "Jos Tin Sorting Enterprises": ["Jos South Sorting Shed"],
}

# Plausible answers keyed by prompt. Anything the checklist asks for that is
# not named here is answered generically, so the seed follows the checklist
# rather than restating it.
ANSWERS = {
    "Registered name": None,  # filled from the company
    "CAC RC number": None,
    "Tax Identification Number (TIN)": None,
    "Company type": "Private Limited (Ltd)",
    "Directors declared": "4",
    "Beneficial ownership disclosed": "Yes",
    "Mining/Processing licence class": "Mineral Processing Licence",
    "Issuing authority": "Mining Cadastre Office",
    "NESREA registration": "Registered",
    "State environmental permit": "Held",
    "Site address": None,
    "Land title / C of O": "Held",
    "Site area": "4.8 hectares",
    "Perimeter security": "Fenced, 24h manned gate",
    "Weighbridge": "Calibrated 60t bridge",
    "Power source": "Grid + 500kVA standby",
    "EIA / EMP status": "EMP approved",
    "Effluent discharge route": "Closed loop, no surface discharge",
    "Air quality monitoring": "Quarterly, third-party",
    "Last effluent test": "Most recent quarter",
    "Community grievance log": "Maintained",
    "HSE officer appointed": "Yes: full-time",
    "PPE issuance register": "Maintained",
    "Lost-time injuries (12 mo)": "1",
    "Emergency drill frequency": "Quarterly",
    "Workforce medical screening": "Annual",
    "Primary process line": None,
    "Installed capacity": None,
    "Maintenance regime": "Planned preventive, monthly",
    "Calibration programme": "Annual, third-party",
    "Batch traceability system": "Beldium Batch ID enabled",
    "Input source verification": "Supplier due-diligence file",
    "Shift logging": "Digital run sheets",
    "Chain of custody": "Documented per run",
    "Reconciliation frequency": "Per production run",
    "On-site laboratory": "Yes",
    "Assay method": "XRF + wet chemistry cross-check",
    "Third-party verification lab": "Engaged",
    "Retention sample policy": "90 days per output batch",
    "Waste streams identified": "Slag, wash sludge, packaging",
    "Licensed waste handler": "Contracted",
    "Tailings storage": "Lined containment cell",
    "Waste manifest system": "In use",
    "Preferred inspection window": "Weekdays, 09:00-15:00",
    "Site access constraints": "Escort required beyond gatehouse",
    "Previous inspection": "None on record",
    "Self-declared readiness": "Ready",
}

# Prompts whose answer is a green flag rather than a neutral statement.
POSITIVE_FLAGS = {
    "Beneficial ownership disclosed",
    "NESREA registration",
    "State environmental permit",
    "Land title / C of O",
    "Effluent discharge route",
    "Batch traceability system",
}

WARN_FLAGS = {"Lost-time injuries (12 mo)"}

# Sections left deliberately short of evidence on the two in-flight
# applications, so the "missing evidence" path and the completeness figure it
# drives are both visible in the demo register.
INCOMPLETE_SECTIONS = {SectionKey.WASTE, SectionKey.INSPECTION}

APPLICATIONS = [
    ("Ilesa Mineral Processing Ltd", ApplicationStage.IN_REVIEW, "", 28, None),
    ("Port Harcourt Metal Recovery Ltd", ApplicationStage.INSPECTION, "", 44, None),
    ("Kaduna Aggregate Crushing Co.", ApplicationStage.AWAITING_INFO, "", 60, None),
    ("Ogun Industrial Minerals Plc", ApplicationStage.DECIDED, ApplicationDecision.APPROVED, 95, 62),
    ("Jos Tin Sorting Enterprises", ApplicationStage.DECIDED, ApplicationDecision.APPROVED, 60, 21),
    ("Enugu Coal Preparation Ltd", ApplicationStage.DECIDED, ApplicationDecision.REJECTED, 120, 88),
]

RISK_CAUSES = {
    "Ilesa Mineral Processing Ltd": [
        ("Chemical/refining process class", 22, "Acid leaching and solvent extraction on site raises inherent process hazard weighting."),
        ("Expiring NESREA registration", 16, "Facility registration expires within 60 days."),
        ("Effluent containment design gaps", 14, "Bund capacity calculation not provided for the reagent store."),
        ("One lost-time injury in 12 months", 9, "Recorded LTI with an incomplete corrective-action file."),
        ("No prior physical inspection", 7, "First-time applicant with no verified site history."),
    ],
    "Port Harcourt Metal Recovery Ltd": [
        ("Smelting process class", 20, "High-temperature recovery with stack emissions and slag handling."),
        ("Open critical environmental alert", 18, "PM10 exceedance recorded at the stack."),
        ("Slag containment finding", 12, "Stockpile observed outside the lined containment cell."),
    ],
    "Kaduna Aggregate Crushing Co.": [
        ("Dust suppression coverage incomplete", 11, "Secondary crusher spray coverage not shown on the submitted layout."),
        ("Expired pressure vessel test", 15, "Integrity test lapsed and has not been renewed."),
    ],
    "Ogun Industrial Minerals Plc": [("Multi-site operation", 8, "Three facilities under one licence increase oversight burden.")],
    "Jos Tin Sorting Enterprises": [("First application in cycle", 6, "Awaiting first evidence review.")],
    "Enugu Coal Preparation Ltd": [
        ("Suspended registration", 30, "Register status suspended pending remediation."),
        ("Three open findings", 24, "Outstanding corrective actions across waste and safety."),
        ("Effluent exceedances", 14, "Repeat wash-water quality failures."),
    ],
}

FINDINGS = [
    ("Port Harcourt Metal Recovery Ltd", SectionKey.WASTE, NonConformity.Severity.MAJOR, "Slag storage exceeds designated containment area", "Slag stockpile observed outside the lined containment cell during pre-inspection photo review.", -34, 26, NonConformity.Status.EVIDENCE_SUBMITTED),
    ("Ilesa Mineral Processing Ltd", SectionKey.ENVIRONMENTAL, NonConformity.Severity.CRITICAL, "Reagent bund capacity calculation not provided", "The EMP annex omits the 110% bund capacity calculation for the acid reagent store.", -29, 14, NonConformity.Status.OPEN),
    ("Kaduna Aggregate Crushing Co.", SectionKey.FACILITY, NonConformity.Severity.MINOR, "Dust suppression coverage map incomplete", "Secondary crusher spray coverage is not shown on the submitted site layout.", -41, -11, NonConformity.Status.OPEN),
    ("Ogun Industrial Minerals Plc", SectionKey.HEALTH_SAFETY, NonConformity.Severity.MINOR, "PPE register gaps for contract workforce", "Issuance records are missing for 11 contract staff in the March cycle.", -69, -39, NonConformity.Status.CLOSED),
    ("Enugu Coal Preparation Ltd", SectionKey.ENVIRONMENTAL, NonConformity.Severity.CRITICAL, "Wash water discharged outside permitted route", "Discharge observed to a surface drain rather than the recycling circuit.", -95, -65, NonConformity.Status.OPEN),
]

ALERTS = [
    ("Port Harcourt Metal Recovery Ltd", "Trans-Amadi Recovery Works", "Rivers", "Stack particulate (PM10)", "128 µg/m³", "100 µg/m³", EnvironmentalAlert.Severity.CRITICAL, -22, EnvironmentalAlert.Status.OPEN),
    ("Kaduna Aggregate Crushing Co.", "Chikun Quarry Plant", "Kaduna", "Ambient dust (TSP)", "231 µg/m³", "200 µg/m³", EnvironmentalAlert.Severity.WARNING, -25, EnvironmentalAlert.Status.ACKNOWLEDGED),
    ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "Osun", "Effluent pH", "5.4", "6.0 - 9.0", EnvironmentalAlert.Severity.WARNING, -31, EnvironmentalAlert.Status.OPEN),
    ("Ogun Industrial Minerals Plc", "Ewekoro Milling Complex", "Ogun", "Noise (boundary, night)", "58 dB", "55 dB", EnvironmentalAlert.Severity.WARNING, -41, EnvironmentalAlert.Status.RESOLVED),
]

INCIDENTS = [
    ("Port Harcourt Metal Recovery Ltd", "Trans-Amadi Recovery Works", "Rivers", "Uncontrolled emission event", Incident.Severity.SEVERE, -23, Incident.Status.UNDER_INVESTIGATION, "Baghouse bypass during furnace tap led to a visible stack plume for around 40 minutes."),
    ("Kaduna Aggregate Crushing Co.", "Chikun Quarry Plant", "Kaduna", "Equipment failure", Incident.Severity.MODERATE, -38, Incident.Status.UNDER_INVESTIGATION, "Secondary crusher bearing seizure; no injuries, production halted 11 hours."),
    ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "Osun", "Chemical spill (contained)", Incident.Severity.MODERATE, -50, Incident.Status.CLOSED, "12 litres of sulphuric acid released within the bunded area; neutralised on site."),
    ("Ogun Industrial Minerals Plc", "Ewekoro Milling Complex", "Ogun", "Lost-time injury", Incident.Severity.LOW, -87, Incident.Status.CLOSED, "Hand laceration during belt maintenance; 3 days lost, retraining completed."),
]

RUNS = [
    ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "BLD-IN-2026-004182", "Ilesa Artisanal Cooperative · Osun", 24_600, "Acid leach → solvent extraction", -26, "BLD-OUT-2026-001907", 8_200, "98.4% purity", "0.6%", TraceabilityRun.Verdict.PASS, "On-site XRF + third-party cross-check"),
    ("Kaduna Aggregate Crushing Co.", "Chikun Quarry Plant", "BLD-IN-2026-004155", "Chikun Pit 3 · Kaduna", 310_000, "Primary crush → secondary crush → screening", -28, "BLD-OUT-2026-001884", 296_400, "Grade 2 aggregate", "2.1%", TraceabilityRun.Verdict.PASS, "On-site sieve analysis"),
    ("Port Harcourt Metal Recovery Ltd", "Trans-Amadi Recovery Works", "BLD-IN-2026-004098", "Trans-Amadi Scrap Aggregators · Rivers", 51_300, "Sort → furnace smelt → casting", -31, "BLD-OUT-2026-001860", 34_700, "Cu 96.1%", "n/a", TraceabilityRun.Verdict.HOLD, "Third-party lab: retest requested"),
    ("Ogun Industrial Minerals Plc", "Ewekoro Milling Complex", "BLD-IN-2026-004201", "Ewekoro Quarry · Ogun", 480_000, "Crush → mill → classify", -12, "BLD-OUT-2026-001930", 461_000, "Grade 1 aggregate", "1.4%", TraceabilityRun.Verdict.PASS, "On-site sieve analysis"),
]

INSPECTIONS = [
    ("Port Harcourt Metal Recovery Ltd", "Trans-Amadi Recovery Works", "Rivers", 13, "Eng. Musa Ibrahim", Inspection.Type.PRE_APPROVAL, Inspection.Status.SCHEDULED, ""),
    ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "Osun", None, "", Inspection.Type.PRE_APPROVAL, Inspection.Status.REQUESTED, ""),
    ("Kaduna Aggregate Crushing Co.", "Chikun Quarry Plant", "Kaduna", -26, "Mrs. Halima Yusuf", Inspection.Type.FOLLOW_UP, Inspection.Status.COMPLETED, "2 minor findings: dust suppression, signage"),
    ("Ogun Industrial Minerals Plc", "Ewekoro Milling Complex", "Ogun", -53, "Eng. Musa Ibrahim", Inspection.Type.ROUTINE, Inspection.Status.COMPLETED, "No major findings"),
    ("Enugu Coal Preparation Ltd", "Udi Coal Wash Plant", "Enugu", 27, "Mrs. Halima Yusuf", Inspection.Type.INCIDENT_TRIGGERED, Inspection.Status.SCHEDULED, ""),
]

# (report kind, scope, period, days since it was generated). The seed compiles
# these for real, so the demo library downloads actual documents.
REPORTS = [
    (ReportKind.NATIONAL, "All regions", "last_quarter", 59),
    (ReportKind.ENVIRONMENTAL, "All regions", "last_month", 37),
    (ReportKind.INSPECTIONS, "All regions", "year_to_date", 66),
    (ReportKind.NATIONAL, "South West", "last_month", 70),
]


# Days to expiry, spread so the register shows valid, expiring and expired
# evidence side by side. Deterministic per document name, so re-seeding does
# not reshuffle which certificate is the lapsed one.
EXPIRY_SPREAD = [720, 400, 200, 51, 42, -8, 900, 120]


def name_hash(name):
    return sum(ord(character) for character in name)


def placeholder_pdf(title):
    """A one-page PDF, so seeded evidence downloads as a real file."""
    body = f"BT /F1 12 Tf 72 720 Td (Beldium demo evidence: {title[:60]}) Tj ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n{obj}\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    return out.encode("latin-1")


class Command(BaseCommand):
    help = "Seed the processing compliance register with demo data."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete existing demo rows before seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260909)
        today = timezone.localdate()
        now = timezone.now()

        organisation, _ = Organisation.objects.get_or_create(
            name=DEMO_ORG,
            defaults={"organisation_type": OrganisationType.MINING_COMPANY, "verification_status": "verified", "country": "Nigeria"},
        )

        if options["flush"]:
            Processor.objects.filter(organisation=organisation).delete()
            ProcessingApplication.objects.filter(organisation=organisation).delete()
            ComplianceReport.objects.filter(reference__in=[r[0] for r in REPORTS]).delete()

        processors = {}
        for name, rc, tin, ptype, state, lga, region, status, score, facility_count in PROCESSORS:
            processor, _ = Processor.objects.update_or_create(
                name=name,
                defaults={
                    "organisation": organisation,
                    "rc_number": rc,
                    "tin": tin,
                    "processing_type": ptype,
                    "state": state,
                    "lga": lga,
                    "region": region,
                    "status": status,
                    "compliance_score": score,
                    "registered_on": today - timedelta(days=random.randint(200, 2200)),
                },
            )
            processors[name] = processor
            for facility_name in FACILITY_NAMES[name][:facility_count]:
                Facility.objects.update_or_create(
                    processor=processor,
                    name=facility_name,
                    defaults={
                        "state": state,
                        "lga": lga,
                        "capacity": f"{random.choice([120, 180, 310, 480])} t/month",
                        "workforce": random.randint(24, 140),
                    },
                )

        applications = {}
        for company, stage, decision, submitted_days_ago, decided_days_ago in APPLICATIONS:
            processor = processors[company]
            facility = processor.facilities.first()
            application, _ = ProcessingApplication.objects.update_or_create(
                company=company,
                defaults={
                    "processor": processor,
                    "organisation": organisation,
                    "rc_number": processor.rc_number,
                    "tin": processor.tin,
                    "processing_type": processor.processing_type,
                    "state": processor.state,
                    "lga": processor.lga,
                    "facility_name": facility.name if facility else "",
                    "capacity": facility.capacity if facility else "",
                    "workforce": facility.workforce if facility else 0,
                    "contact_name": "Adebayo Ogunleye",
                    "contact_email": f"compliance@{company.split()[0].lower()}.ng",
                    "contact_phone": "+234 803 441 2290",
                    "stage": stage,
                    "decision": decision,
                    "decided_at": now - timedelta(days=decided_days_ago) if decided_days_ago is not None else None,
                    "decision_note": "Approved subject to routine surveillance." if decision == ApplicationDecision.APPROVED else ("Rejected: unresolved critical findings." if decision else ""),
                    "submitted_on": today - timedelta(days=submitted_days_ago),
                },
            )
            applications[company] = application

            # Answers follow the checklist rather than a second copy of it, so
            # a change to the compliance requirements is reflected here too.
            per_company = {
                "Registered name": company,
                "CAC RC number": processor.rc_number,
                "Tax Identification Number (TIN)": processor.tin,
                "Site address": f"{application.facility_name}, {processor.lga} LGA, {processor.state} State",
                "Primary process line": processor.get_processing_type_display(),
                "Installed capacity": application.capacity,
            }
            short = stage in {ApplicationStage.NEW, ApplicationStage.AWAITING_INFO}
            for key, _label in SectionKey.choices:
                leave_short = short and key in INCOMPLETE_SECTIONS
                fields = []
                for label, required in checklist.PROMPTS[key]:
                    if leave_short and required:
                        continue
                    value = per_company.get(label) or ANSWERS.get(label) or "Confirmed"
                    flag = "ok" if label in POSITIVE_FLAGS else ("warn" if label in WARN_FLAGS else None)
                    fields.append({"label": label, "value": value, **({"flag": flag} if flag else {})})
                review_state = ReviewState.VERIFIED if stage == ApplicationStage.DECIDED else ReviewState.PENDING
                ApplicationSection.objects.update_or_create(
                    application=application,
                    key=key,
                    defaults={"fields": fields, "review_state": review_state, "reviewed_at": now if review_state == ReviewState.VERIFIED else None},
                )

            application.risk_causes.all().delete()
            RiskCause.objects.bulk_create(
                [RiskCause(application=application, cause=cause, weight=weight, detail=detail) for cause, weight, detail in RISK_CAUSES[company]]
            )

            application.documents.all().delete()
            for section, name, issuer, expires in checklist.documents_for(processor.processing_type):
                document = ProcessingDocument.objects.create(
                    application=application,
                    processor=processor,
                    section=section,
                    name=name,
                    reference=f"{name.split()[0].upper()[:8]}-{random.randint(1000, 9999)}",
                    issuer=issuer,
                    issued_on=today - timedelta(days=random.randint(60, 900)),
                    expires_on=today + timedelta(days=EXPIRY_SPREAD[name_hash(name) % len(EXPIRY_SPREAD)]) if expires else None,
                )
                if not (short and section in INCOMPLETE_SECTIONS):
                    # Validity and completeness both key off a file being
                    # present, so a demo without one reads as 0% everywhere.
                    document.file.save(f"{document.reference}.pdf", ContentFile(placeholder_pdf(name)), save=True)

        NonConformity.objects.filter(application__in=applications.values()).delete()
        for company, section, severity, title, detail, raised_offset, due_offset, status in FINDINGS:
            finding = NonConformity.objects.create(
                application=applications[company],
                processor=processors[company],
                section=section,
                severity=severity,
                title=title,
                detail=detail,
                raised_on=today + timedelta(days=raised_offset),
                due_on=today + timedelta(days=due_offset),
                status=status,
                closed_at=now if status == NonConformity.Status.CLOSED else None,
            )
            if status != NonConformity.Status.OPEN:
                NonConformityEvidence.objects.create(
                    non_conformity=finding,
                    name="Corrective action evidence pack",
                    note="Photographic evidence and handler manifest attached.",
                )

        EnvironmentalAlert.objects.filter(processor__in=processors.values()).delete()
        for company, facility_name, state, parameter, reading, threshold, severity, detected_offset, status in ALERTS:
            EnvironmentalAlert.objects.create(
                processor=processors[company],
                facility=processors[company].facilities.filter(name=facility_name).first(),
                facility_name=facility_name,
                state=state,
                parameter=parameter,
                reading=reading,
                threshold=threshold,
                severity=severity,
                detected_on=today + timedelta(days=detected_offset),
                status=status,
                acknowledged_at=now if status != EnvironmentalAlert.Status.OPEN else None,
                resolved_at=now if status == EnvironmentalAlert.Status.RESOLVED else None,
            )

        Incident.objects.filter(processor__in=processors.values()).delete()
        for company, facility_name, state, incident_type, severity, reported_offset, status, summary in INCIDENTS:
            Incident.objects.create(
                processor=processors[company],
                facility=processors[company].facilities.filter(name=facility_name).first(),
                facility_name=facility_name,
                state=state,
                incident_type=incident_type,
                severity=severity,
                reported_on=today + timedelta(days=reported_offset),
                status=status,
                summary=summary,
                closed_at=now if status == Incident.Status.CLOSED else None,
            )

        TraceabilityRun.objects.filter(processor__in=processors.values()).delete()
        for company, facility_name, input_batch, source, input_kg, process, started_offset, output_batch, output_kg, assay, moisture, verdict, lab in RUNS:
            started = now + timedelta(days=started_offset)
            TraceabilityRun.objects.create(
                processor=processors[company],
                facility=processors[company].facilities.filter(name=facility_name).first(),
                facility_name=facility_name,
                input_batch=input_batch,
                input_source=source,
                input_mass_kg=input_kg,
                process=process,
                started_at=started,
                completed_at=started + timedelta(hours=36),
                output_batch=output_batch,
                output_mass_kg=output_kg,
                qc_assay=assay,
                qc_moisture=moisture,
                qc_verdict=verdict,
                qc_lab=lab,
            )

        Inspection.objects.filter(processor__in=processors.values()).delete()
        for company, facility_name, state, scheduled_offset, inspector, inspection_type, status, outcome in INSPECTIONS:
            Inspection.objects.create(
                application=applications.get(company),
                processor=processors[company],
                facility=processors[company].facilities.filter(name=facility_name).first(),
                facility_name=facility_name,
                state=state,
                scheduled_for=None if scheduled_offset is None else today + timedelta(days=scheduled_offset),
                inspector_name=inspector,
                inspection_type=inspection_type,
                status=status,
                outcome=outcome,
                completed_at=now if status == Inspection.Status.COMPLETED else None,
            )

        ComplianceReport.objects.all().delete()
        for kind, scope, period, generated_days_ago in REPORTS:
            report = ComplianceReport(
                kind=kind,
                title=ReportKind.LABELS[kind],
                scope=scope,
                generated_on=today - timedelta(days=generated_days_ago),
            )
            report.reference = generate_report_reference()
            pdf, pages, period_label = build_report(kind, scope, period, reference=report.reference)
            report.period_label = period_label
            report.pages = pages
            report.save()
            report.file.save(f"{report.reference}.pdf", ContentFile(pdf), save=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(processors)} processors, {len(applications)} applications, "
                f"{len(FINDINGS)} findings, {len(INSPECTIONS)} inspections, {len(RUNS)} runs."
            )
        )
