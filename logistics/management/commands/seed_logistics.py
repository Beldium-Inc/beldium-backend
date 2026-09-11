"""Populate the logistics compliance app with a representative demo register."""
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from logistics import services
from logistics.models import (
    ApprovalCondition,
    Domain,
    InformationRequest,
    LogisticsAccessGrant,
    LogisticsApplication,
    LogisticsCompany,
    LogisticsDocument,
    LogisticsReport,
    MonitoringEvent,
    Notification,
    OperatingLocation,
    ScopeRestriction,
    Vehicle,
    Driver,
)
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType


DEMO_PREFIX = "Beldium Logistics Demo"

COMPANIES = [
    {
        "name": "Ilesa Heavy Haulage Ltd",
        "registration_number": "LOG-RC-1428907",
        "status": "under_review",
        "services": ["mineral haulage", "general freight"],
        "score": 72,
        "state": "Osun",
    },
    {
        "name": "Trans-Sahel Mineral Logistics Plc",
        "registration_number": "LOG-RC-501992",
        "status": "conditionally_approved",
        "services": ["mineral haulage", "escort convoy", "bulk freight"],
        "score": 81,
        "state": "Kaduna",
    },
    {
        "name": "Port Harcourt Secure Freight Ltd",
        "registration_number": "LOG-RC-772104",
        "status": "approved",
        "services": ["general freight", "hazardous materials"],
        "score": 91,
        "state": "Rivers",
    },
]


def user(email, **extra):
    defaults = {
        "first_name": extra.pop("first_name", ""),
        "last_name": extra.pop("last_name", ""),
        "email_verified_at": timezone.now(),
        **extra,
    }
    person, created = User.objects.get_or_create(email=email, defaults=defaults)
    if created:
        person.set_password("DemoPass-2026!")
        person.save(update_fields=["password"])
    else:
        for field, value in defaults.items():
            setattr(person, field, value)
        person.save()
    return person


def demo_pdf(title):
    body = f"BT /F1 12 Tf 72 720 Td (Beldium logistics demo evidence: {title[:50]}) Tj ET"
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        + f"4 0 obj << /Length {len(body)} >> stream\n{body}\nendstream endobj\n".encode()
        + b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )
    return ContentFile(content, name=f"{title.lower().replace(' ', '-')}.pdf")


class Command(BaseCommand):
    help = "Seed logistics companies, applications, evidence, alerts, notifications and reports."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Remove the previous logistics demo data first.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            demo_orgs = Organisation.objects.filter(name__startswith=DEMO_PREFIX)
            LogisticsCompany.objects.filter(organisation__in=demo_orgs).delete()
            demo_orgs.delete()
            User.objects.filter(email__endswith="@logistics-demo.test").delete()

        owner = user("company@logistics-demo.test", first_name="Ada", last_name="Bello")
        reviewer = user("reviewer@logistics-demo.test", first_name="Musa", last_name="Ibrahim")
        regulator = user("regulator@logistics-demo.test", first_name="Halima", last_name="Yusuf")

        created_companies = []
        for index, row in enumerate(COMPANIES, start=1):
            org, _ = Organisation.objects.update_or_create(
                registration_number=row["registration_number"],
                defaults={
                    "name": f"{DEMO_PREFIX}: {row['name']}",
                    "organisation_type": OrganisationType.MINING_COMPANY,
                    "verification_status": "verified",
                    "state": row["state"],
                    "country": "Nigeria",
                    "email": f"ops{index}@logistics-demo.test",
                },
            )
            OrganisationMembership.objects.update_or_create(
                organisation=org,
                user=owner,
                defaults={"role": MembershipRole.OWNER, "is_active": True},
            )
            company, _ = LogisticsCompany.objects.update_or_create(
                organisation=org,
                defaults={
                    "contact_name": "Ada Bello",
                    "contact_email": f"ops{index}@logistics-demo.test",
                    "contact_phone": f"+23480000000{index}",
                    "employees": 18 + index * 7,
                    "annual_tonnage": 3000 + index * 2400,
                    "services": row["services"],
                },
            )
            LogisticsAccessGrant.objects.update_or_create(company=company, user=reviewer, role="reviewer", defaults={"is_active": True})
            LogisticsAccessGrant.objects.update_or_create(company=company, user=regulator, role="regulator", defaults={"is_active": True})
            created_companies.append((company, row))

            OperatingLocation.objects.update_or_create(
                company=company,
                name=f"{row['state']} Logistics Yard",
                defaults={
                    "location_type": "depot",
                    "address": f"Industrial Layout, {row['state']}",
                    "state": row["state"],
                    "staff_count": 12 + index,
                },
            )
            vehicle, _ = Vehicle.objects.update_or_create(
                registration=f"BLD-LG-{index:03d}",
                defaults={
                    "company": company,
                    "vin": f"DEMOLOGISTICSVIN{index:03d}",
                    "vehicle_type": "Articulated truck",
                    "make": "MAN",
                    "model": "TGS",
                    "year": timezone.localdate().year - 1,
                    "capacity": "32.00",
                    "capacity_unit": "tonnes",
                    "ownership": "owned",
                    "insurer": "Beldium Demo Insurance",
                    "insurance_expiry": timezone.localdate() + timedelta(days=20 if index == 2 else 180),
                    "roadworthiness_expiry": timezone.localdate() + timedelta(days=-3 if index == 1 else 120),
                    "gps_status": "active" if index != 1 else "intermittent",
                    "location": row["state"],
                },
            )
            Driver.objects.update_or_create(
                licence_number=f"DRV-DEMO-{index:03d}",
                defaults={
                    "company": company,
                    "full_name": ["Musa Lawal", "Grace Okon", "Kabiru Danjuma"][index - 1],
                    "licence_class": "G",
                    "licence_expiry": timezone.localdate() + timedelta(days=90),
                    "medical_expiry": timezone.localdate() + timedelta(days=18 if index == 3 else 160),
                    "assigned_vehicle": vehicle,
                    "training": ["defensive driving", "mineral custody"],
                    "years_experience": 6 + index,
                    "is_active": True,
                },
            )

            application, _ = LogisticsApplication.objects.update_or_create(
                company=company,
                defaults={
                    "created_by": owner,
                    "reviewer": reviewer,
                    "status": row["status"],
                    "submitted_at": timezone.now() - timedelta(days=20 + index * 4),
                    "rationale": "Seeded logistics demo application.",
                },
            )
            if not application.sections.exists():
                services.initialise(application)
            application.domain_weights = {key: 1 for key in Domain.values}
            application.save(update_fields=["domain_weights"])
            application.sections.update(data={"declared": True}, status="passed", score=row["score"], reviewed_by=reviewer, reviewed_at=timezone.now())
            if row["status"] == "under_review":
                application.sections.filter(key=Domain.INSURANCE).update(status="attention", score=58, review_notes="Insurance certificate expires inside the warning window.")

            for domain in Domain.values:
                document_type = f"{domain}_evidence"
                previous = LogisticsDocument.objects.filter(application=application, document_type=document_type, is_current=True).first()
                if previous:
                    previous.delete()
                LogisticsDocument.objects.create(
                    application=application,
                    domain=domain,
                    document_type=document_type,
                    title=f"{domain.replace('_', ' ').title()} evidence pack",
                    issuer="Beldium Demo",
                    expires_on=timezone.localdate() + timedelta(days=14 if domain in {Domain.REGULATORY, Domain.INSURANCE} else 365),
                    service_scope=row["services"][0] if domain in {Domain.REGULATORY, Domain.INSURANCE, Domain.MINERAL} else "",
                    status="verified" if domain != Domain.INSURANCE or row["status"] != "under_review" else "pending",
                    file=demo_pdf(f"{row['name']} {domain}"),
                    original_name=f"{domain}-evidence.pdf",
                    uploaded_by=owner,
                    reviewed_by=reviewer,
                    reviewed_at=timezone.now(),
                )

            if row["status"] == "conditionally_approved":
                condition, _ = ApprovalCondition.objects.update_or_create(
                    application=application,
                    title="Renew expiring goods-in-transit cover",
                    defaults={
                        "description": "Provide renewed policy before expanding mineral-haulage scope.",
                        "due_date": timezone.localdate() + timedelta(days=10),
                        "service_scope": row["services"][0],
                    },
                )
                ScopeRestriction.objects.update_or_create(
                    company=company,
                    service_scope=row["services"][0],
                    source_condition=condition,
                    defaults={"reason": condition.title, "applied_by": reviewer},
                )

            if row["status"] != "approved":
                InformationRequest.objects.update_or_create(
                    application=application,
                    reason="Evidence clarification",
                    defaults={
                        "message": "Please confirm renewal timing and attach the latest supporting evidence.",
                        "items": ["renewal timing", "supporting evidence"],
                        "due_date": timezone.localdate() + timedelta(days=7),
                        "raised_by": reviewer,
                    },
                )

            MonitoringEvent.objects.update_or_create(
                event_key=f"demo-logistics-{company.pk}-expiry",
                defaults={
                    "company": company,
                    "kind": "expiring",
                    "message": f"{row['name']} has credentials inside the 30-day warning window.",
                },
            )
            Notification.objects.get_or_create(
                company=company,
                recipient=owner,
                title="Logistics demo data ready",
                body=f"{row['name']} is available in the logistics demo register.",
            )

        output = "Reference,Company,Application status\n"
        output += "".join(f"{company.reference},{company.organisation.name},{row['status']}\n" for company, row in created_companies)
        LogisticsReport.objects.update_or_create(
            requested_by=owner,
            report_type="register",
            defaults={"company_ids": [str(company.pk) for company, _ in created_companies], "content": output},
        )

        services.monitor_expiries()
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(created_companies)} logistics companies."))
