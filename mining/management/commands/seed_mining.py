"""Populate the mining register with a minimal smoke-test dataset.

The connected frontend has no data of its own; every screen reads the API. A
fresh database otherwise gives every mining screen an empty state, so this
creates just enough real records — one site worth seeing, one row of each
activity type — to exercise every path the UI renders without shipping a
large block of invented company data.

Attached to ``Beldium Mining Demo Co`` — the same organisation
``seed_test_accounts`` gives ``mining.applicant@beldium.test`` (role
``miner``) — so that account sees real data rather than an empty register.
Also creates enough ``Application``/``PendingReview``/``InfoRequest`` rows
that the partner/regulator desk views (which share this same register) are
not empty either.

Idempotent: it keys on the demo organisation/site, so re-running refreshes
the data rather than duplicating it.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from organisations.models import Organisation
from mining.models import (
    Application,
    CorrectiveSubmission,
    DocumentRecord,
    EnvRecord,
    Equipment,
    Evidence,
    InfoRequest,
    InventoryItem,
    Inspection,
    LicenceDoc,
    MineSite,
    NonConformity,
    PendingReview,
    ProductionRecord,
    ReviewSection,
    Sample,
    SafetyIncident,
    ScoreFactor,
    SectionKey,
    SiteStatus,
)

DEMO_ORG = "Beldium Mining Demo Co"
SITE_NAME = "Ijero Lithium Pit A"

SECTIONS = [
    (SectionKey.CORPORATE, "Verified", [
        {"label": "Registered name", "value": DEMO_ORG},
        {"label": "CAC RC number", "value": "RC 812004"},
        {"label": "Directors declared", "value": "3", "flag": "ok"},
    ]),
    (SectionKey.LICENCE, "Verified", [
        {"label": "Mining licence class", "value": "Small Scale Mining Lease"},
        {"label": "Issuing authority", "value": "Mining Cadastre Office", "flag": "ok"},
    ]),
    (SectionKey.SITE, "Under review", [
        {"label": "Site area", "value": "12.4 hectares"},
        {"label": "Perimeter security", "value": "Fenced, manned gate"},
    ]),
]

EQUIPMENT = [
    ("Excavator CAT 320", "EXC-2201", 240, Equipment.Status.CERTIFIED),
    ("Jaw Crusher JC-500", "JC-500-19", -12, Equipment.Status.DUE_INSPECTION),
    ("Haul Truck HT-40", "HT-40-07", 400, Equipment.Status.CERTIFIED),
]


class Command(BaseCommand):
    help = "Seed the mining register with a minimal smoke-test dataset."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Delete existing demo rows before seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        today = timezone.localdate()
        now = timezone.now()

        try:
            organisation = Organisation.objects.get(name=DEMO_ORG)
        except Organisation.DoesNotExist:
            raise CommandError(
                f"Organisation '{DEMO_ORG}' not found. Run `manage.py seed_test_accounts` first "
                "so mining.applicant@beldium.test's organisation exists."
            )

        if options["flush"]:
            MineSite.objects.filter(organisation=organisation, name=SITE_NAME).delete()

        site, _ = MineSite.objects.update_or_create(
            organisation=organisation,
            name=SITE_NAME,
            defaults={
                "mineral": "Lithium",
                "state": "Ekiti",
                "lga": "Ijero",
                "latitude": "7.812000",
                "longitude": "5.068000",
                "area_ha": "12.40",
                "status": SiteStatus.OPERATIONAL,
                "compliance_score": 74,
                "risk": "medium",
                "capacity_tpa": 60000,
                "current_tpa": 41000,
                "workforce": 85,
                "last_inspection_on": today - timedelta(days=40),
                "verification": {"site": True, "licence": True, "documents": True, "gps": True},
                "risk_reasons": [
                    {"label": "Equipment inspection overdue", "detail": "Jaw crusher certification lapsed 12 days ago."},
                ],
                "production": [
                    {"month": "2026-06", "tonnes": 3400, "grade": 1.2},
                    {"month": "2026-07", "tonnes": 3650, "grade": 1.3},
                    {"month": "2026-08", "tonnes": 3800, "grade": 1.25},
                ],
                "inventory": [
                    {"item": "Spodumene concentrate", "qty": 820, "location": "Stockpile A", "updated": str(today - timedelta(days=2))},
                    {"item": "Diesel", "qty": 4200, "location": "Fuel farm", "updated": str(today - timedelta(days=1))},
                ],
                "transactions": [
                    {"date": str(today - timedelta(days=20)), "buyer": "Ilesa Verified Minerals Ltd", "tonnes": 500, "value": 185000, "status": "completed"},
                    {"date": str(today - timedelta(days=5)), "buyer": "Beldium Marketplace Demo: Test Seller Co", "tonnes": 300, "value": 111000, "status": "pending"},
                ],
            },
        )

        # -- review sections + evidence -------------------------------------
        site.sections.all().delete()
        for key, status_label, fields in SECTIONS:
            state = "verified" if status_label == "Verified" else "under_review"
            section = ReviewSection.objects.create(
                site=site,
                key=key,
                title=dict(SectionKey.choices)[key],
                summary=f"{status_label} on {today.isoformat()}.",
                weight=10,
                score=90 if state == "verified" else 55,
                status=state,
                fields=fields,
            )
            Evidence.objects.create(
                section=section,
                name=f"{section.title} evidence pack.pdf",
                kind=Evidence.Kind.PDF,
                status=Evidence.Status.VERIFIED if state == "verified" else Evidence.Status.PENDING,
            )

        # -- score factors ----------------------------------------------------
        site.score_factors.all().delete()
        ScoreFactor.objects.create(
            site=site, label="Licence & permits current", weight=30, score=95,
            reason="All permits active, none expiring within 60 days.", trend="up",
        )
        ScoreFactor.objects.create(
            site=site, label="Equipment certification", weight=20, score=60,
            reason="Jaw crusher inspection overdue.", trend="down",
        )

        # -- non-conformity + corrective submission ---------------------------
        site.non_conformities.all().delete()
        nc = NonConformity.objects.create(
            site=site,
            title="Jaw crusher inspection overdue",
            category="Equipment",
            severity=NonConformity.Severity.MAJOR,
            required_action="Submit updated inspection certificate for JC-500.",
            responsible_person="Site Safety Officer",
            deadline=today + timedelta(days=14),
            status=NonConformity.Status.IN_PROGRESS,
        )
        CorrectiveSubmission.objects.create(
            non_conformity=nc,
            message="Inspection booked with certified contractor for next week.",
        )

        # -- inspection ---------------------------------------------------------
        site.inspections.all().delete()
        Inspection.objects.create(
            site=site,
            type=Inspection.Type.ROUTINE,
            scheduled_for=today + timedelta(days=10),
            inspector_name="Emeka Regulator",
            status=Inspection.Status.SCHEDULED,
            findings=[{"area": "Equipment yard", "observation": "Crusher certification pending renewal.", "severity": "major"}],
        )

        # -- sample ---------------------------------------------------------------
        site.samples.all().delete()
        Sample.objects.create(
            site=site,
            collected_on=today - timedelta(days=6),
            lab="Nigerian Geological Survey Agency Lab",
            certificate="NGSA-2026-0417",
            li2o_percent="1.28",
            fe2o3_percent="0.42",
            moisture_percent="3.10",
            status=Sample.Status.VERIFIED,
            method="XRF + wet chemistry cross-check",
        )

        # -- licence ----------------------------------------------------------------
        site.licences.all().delete()
        LicenceDoc.objects.create(
            site=site,
            number="SSML-2024-00812",
            type="Small Scale Mining Lease",
            authority="Mining Cadastre Office",
            issued_on=today - timedelta(days=500),
            expires_on=today + timedelta(days=600),
            status=LicenceDoc.Status.ACTIVE,
        )

        # -- documents ---------------------------------------------------------------
        site.documents.all().delete()
        DocumentRecord.objects.create(
            site=site, name="Environmental Management Plan", category="environmental",
            expires_on=today + timedelta(days=300), status=DocumentRecord.Status.VERIFIED,
        )

        # -- equipment -----------------------------------------------------------------
        site.equipment.all().delete()
        for name, serial, expiry_offset, status in EQUIPMENT:
            Equipment.objects.create(
                site=site, name=name, serial=serial,
                cert_expires_on=today + timedelta(days=expiry_offset), status=status,
            )

        # -- safety incident -------------------------------------------------------------
        site.safety_incidents.all().delete()
        SafetyIncident.objects.create(
            site=site, date=today - timedelta(days=45), type="Minor slip - wet surface",
            severity=SafetyIncident.Severity.MINOR, lost_days=1, status=SafetyIncident.Status.CLOSED,
            summary="Worker slipped near wash bay; treated on site, no lost-time beyond one shift.",
        )

        # -- environmental record -------------------------------------------------------
        site.env_records.all().delete()
        EnvRecord.objects.create(
            site=site, metric="Effluent pH", value="6.8", limit="6.0 - 9.0",
            status=EnvRecord.Status.WITHIN_LIMIT, measured_on=today - timedelta(days=3),
        )

        # -- production / inventory rows -------------------------------------------------
        site.production_records.all().delete()
        ProductionRecord.objects.create(
            site=site, period_start=today.replace(day=1) - timedelta(days=1), period_end=today,
            commodity="Spodumene concentrate", tonnage="3800.00", grade="1.250",
        )
        site.inventory_items.all().delete()
        InventoryItem.objects.create(
            site=site, category="stockpile", name="Spodumene concentrate",
            quantity="820.00", unit="tonnes", threshold="200.00",
        )

        # -- applications / pending reviews / info requests (shared register) -----------
        Application.objects.filter(organisation=organisation).delete()
        application = Application.objects.create(
            organisation=organisation,
            site=site,
            site_name=site.name,
            type="Amendment - capacity increase",
            mineral="Lithium",
            submitted_on=today - timedelta(days=8),
            stage="Under review",
            status=Application.Status.UNDER_REVIEW,
            sla_days=30,
        )

        PendingReview.objects.filter(site=site).delete()
        PendingReview.objects.create(
            site=site, subject="Capacity increase amendment review", type="amendment",
            priority=PendingReview.Priority.HIGH, submitted_on=today - timedelta(days=8),
            due_on=today + timedelta(days=7), status=PendingReview.Status.IN_PROGRESS,
        )

        InfoRequest.objects.filter(site=site).delete()
        InfoRequest.objects.create(
            site=site, section=SectionKey.EQUIPMENT, subject="Updated crusher inspection certificate",
            details="Please submit the renewed certification for JC-500-19.",
            due_by=today + timedelta(days=7), priority=InfoRequest.Priority.NORMAL,
            status=InfoRequest.Status.OPEN,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded 1 mine site ({site.name}), 3 review sections, 2 score factors, "
                "1 non-conformity + corrective submission, 1 inspection, 1 sample, 1 licence, "
                "1 document, 3 equipment rows, 1 safety incident, 1 env record, "
                "1 production record, 1 inventory item, 1 application, 1 pending review, 1 info request."
            )
        )
