"""Populate the processing register with a minimal smoke-test dataset.

The connected frontend has no data of its own; every screen reads the API. A
fresh database otherwise gives every processing screen an empty state, so
this creates just enough real records — one processor per lifecycle stage
worth seeing, one row of each activity type — to exercise every path the UI
renders without shipping a large block of invented company data.

Idempotent: it keys on the demo organisation, so re-running refreshes the data
rather than duplicating it.
"""
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

# Two processors: one already decided (approved, so the register shows a
# cleared record), one still in review (so the applicant/desk flows have a
# live case to act on).
PROCESSORS = [
    ("Ilesa Mineral Processing Ltd", "RC 1428907", "20418833-0001", ProcessingType.CHEMICAL_REFINING, "Osun", "Ilesa East", "South West", ProcessorStatus.UNDER_REVIEW, 58),
    ("Ogun Industrial Minerals Plc", "RC 501992", "40119922-0001", ProcessingType.CRUSHING_MILLING, "Ogun", "Ewekoro", "South West", ProcessorStatus.APPROVED, 92),
]

FACILITY_NAMES = {
    "Ilesa Mineral Processing Ltd": "Ilesa Refining Plant A",
    "Ogun Industrial Minerals Plc": "Ewekoro Milling Complex",
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

# Sections left deliberately short of evidence on the in-flight application,
# so the "missing evidence" path and the completeness figure it drives are
# both visible.
INCOMPLETE_SECTIONS = {SectionKey.WASTE, SectionKey.INSPECTION}

APPLICATIONS = [
    ("Ilesa Mineral Processing Ltd", ApplicationStage.IN_REVIEW, "", 28, None),
    ("Ogun Industrial Minerals Plc", ApplicationStage.DECIDED, ApplicationDecision.APPROVED, 95, 62),
]

RISK_CAUSES = {
    "Ilesa Mineral Processing Ltd": [
        ("Chemical/refining process class", 22, "Acid leaching and solvent extraction on site raises inherent process hazard weighting."),
        ("Expiring NESREA registration", 16, "Facility registration expires within 60 days."),
    ],
    "Ogun Industrial Minerals Plc": [
        ("Multi-site operation", 8, "Three facilities under one licence increase oversight burden."),
    ],
}

# One finding, one alert, one incident, one run, one inspection — enough for
# each screen to have a real row without a wall of invented history.
FINDING = ("Ilesa Mineral Processing Ltd", SectionKey.ENVIRONMENTAL, NonConformity.Severity.CRITICAL, "Reagent bund capacity calculation not provided", "The EMP annex omits the 110% bund capacity calculation for the acid reagent store.", -29, 14, NonConformity.Status.OPEN)

ALERT = ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "Osun", "Effluent pH", "5.4", "6.0 - 9.0", EnvironmentalAlert.Severity.WARNING, -31, EnvironmentalAlert.Status.OPEN)

INCIDENT = ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "Osun", "Chemical spill (contained)", Incident.Severity.MODERATE, -50, Incident.Status.CLOSED, "12 litres of sulphuric acid released within the bunded area; neutralised on site.")

RUN = ("Ogun Industrial Minerals Plc", "Ewekoro Milling Complex", "BLD-IN-2026-004201", "Ewekoro Quarry · Ogun", 480_000, "Crush → mill → classify", -12, "BLD-OUT-2026-001930", 461_000, "Grade 1 aggregate", "1.4%", TraceabilityRun.Verdict.PASS, "On-site sieve analysis")

INSPECTION = ("Ilesa Mineral Processing Ltd", "Ilesa Refining Plant A", "Osun", None, "", Inspection.Type.PRE_APPROVAL, Inspection.Status.REQUESTED, "")

# (report kind, scope, period, days since it was generated). The seed compiles
# this for real, so the demo library downloads an actual document.
REPORT = (ReportKind.NATIONAL, "All regions", "last_quarter", 59)

# Days to expiry, spread so the register shows valid, expiring and expired
# evidence side by side. Deterministic per document name, so re-seeding does
# not reshuffle which certificate is the lapsed one.
EXPIRY_SPREAD = [720, 51, -8]


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
    help = "Seed the processing compliance register with a minimal smoke-test dataset."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete existing demo rows before seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        now = timezone.now()

        organisation, _ = Organisation.objects.get_or_create(
            name=DEMO_ORG,
            defaults={"organisation_type": OrganisationType.MINING_COMPANY, "verification_status": "verified", "country": "Nigeria"},
        )

        if options["flush"]:
            Processor.objects.filter(organisation=organisation).delete()
            ProcessingApplication.objects.filter(organisation=organisation).delete()
            ComplianceReport.objects.filter(reference=REPORT).delete()

        processors = {}
        for name, rc, tin, ptype, state, lga, region, status, score in PROCESSORS:
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
                    "registered_on": today - timedelta(days=600),
                },
            )
            processors[name] = processor
            Facility.objects.update_or_create(
                processor=processor,
                name=FACILITY_NAMES[name],
                defaults={"state": state, "lga": lga, "capacity": "180 t/month", "workforce": 60},
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
                    reference=f"{name.split()[0].upper()[:8]}-0001",
                    issuer=issuer,
                    issued_on=today - timedelta(days=300),
                    expires_on=today + timedelta(days=EXPIRY_SPREAD[name_hash(name) % len(EXPIRY_SPREAD)]) if expires else None,
                )
                if not (short and section in INCOMPLETE_SECTIONS):
                    # Validity and completeness both key off a file being
                    # present, so a demo without one reads as 0% everywhere.
                    document.file.save(f"{document.reference}.pdf", ContentFile(placeholder_pdf(name)), save=True)

        NonConformity.objects.filter(application__in=applications.values()).delete()
        company, section, severity, title, detail, raised_offset, due_offset, status = FINDING
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
        )
        if status != NonConformity.Status.OPEN:
            NonConformityEvidence.objects.create(
                non_conformity=finding,
                name="Corrective action evidence pack",
                note="Photographic evidence and handler manifest attached.",
            )

        EnvironmentalAlert.objects.filter(processor__in=processors.values()).delete()
        company, facility_name, state, parameter, reading, threshold, severity, detected_offset, status = ALERT
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
        )

        Incident.objects.filter(processor__in=processors.values()).delete()
        company, facility_name, state, incident_type, severity, reported_offset, status, summary = INCIDENT
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
        company, facility_name, input_batch, source, input_kg, process, started_offset, output_batch, output_kg, assay, moisture, verdict, lab = RUN
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
        company, facility_name, state, scheduled_offset, inspector, inspection_type, status, outcome = INSPECTION
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
        )

        ComplianceReport.objects.all().delete()
        kind, scope, period, generated_days_ago = REPORT
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
                f"1 finding, 1 alert, 1 incident, 1 run, 1 inspection, 1 report."
            )
        )
