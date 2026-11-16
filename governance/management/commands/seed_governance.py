"""Demo records for the governance workspace, for local and staging use only.

Creates one account per governance role (password DemoPass-2026!) and the
policies, risks, findings, stakeholders, milestones and an impact assessment
the workspace screens read. Every demo record belongs to one of the demo
accounts (addresses ending in .demo), which is how --flush finds them without
touching anyone else's records.
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from governance import models as m

DEMO_SUFFIX = ".demo"
PASSWORD = "DemoPass-2026!"

PEOPLE = [
    ("governance.admin@beldium.demo", "Ifeoma", "Admin", m.GovernanceRole.SUPER_ADMIN, "Beldium Internal"),
    ("m.diallo@beldium.demo", "Miriam", "Diallo", m.GovernanceRole.EXECUTIVE, "Beldium Internal"),
    ("a.okafor@beldium.demo", "Ade", "Okafor", m.GovernanceRole.RISK_OFFICER, "Beldium Internal"),
    ("k.adeyemi@beldium.demo", "Kemi", "Adeyemi", m.GovernanceRole.ANALYST, "Beldium Internal"),
    ("t.nwosu@regulator.demo", "Tunde", "Nwosu", m.GovernanceRole.REVIEWER, "Regulatory Institution"),
    ("g.lin@dfi.demo", "Grace", "Lin", m.GovernanceRole.VIEWER, "Development Finance Institution"),
]


def person(email, first, last, role, organisation):
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"first_name": first, "last_name": last, "email_verified_at": timezone.now()},
    )
    if created:
        user.set_password(PASSWORD)
        user.save(update_fields=["password"])
    m.GovernanceMember.objects.update_or_create(
        user=user,
        defaults={"role": role, "status": m.MemberStatus.ACTIVE, "organisation": organisation, "profile": {"seed": "demo"}},
    )
    return user


class Command(BaseCommand):
    help = "Seed demo governance members and records. Never run against production."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Remove previous governance demo records first.")

    @transaction.atomic
    def handle(self, *args, **options):
        from django.conf import settings

        if not settings.DEBUG and "sqlite" not in settings.DATABASES["default"]["ENGINE"]:
            raise CommandError("seed_governance only runs against a local or throwaway database (DEBUG on, or SQLite).")

        if options["flush"]:
            demo = {"email__endswith": DEMO_SUFFIX}
            m.GovernanceDocument.objects.filter(**{f"uploaded_by__{k}": v for k, v in demo.items()}).delete()
            m.ImpactAssessment.objects.filter(**{f"created_by__{k}": v for k, v in demo.items()}).delete()
            m.Milestone.objects.filter(**{f"policy__created_by__{k}": v for k, v in demo.items()}).delete()
            m.PolicyEvent.objects.filter(**{f"policy__created_by__{k}": v for k, v in demo.items()}).delete()
            m.Policy.objects.filter(**{f"created_by__{k}": v for k, v in demo.items()}).delete()
            m.Risk.objects.filter(**{f"created_by__{k}": v for k, v in demo.items()}).delete()
            m.RegulatoryFinding.objects.filter(**{f"recorded_by__{k}": v for k, v in demo.items()}).delete()
            m.Stakeholder.objects.filter(**{f"owner__{k}": v for k, v in demo.items()}).delete()

        users = {email: person(email, first, last, role, org) for email, first, last, role, org in PEOPLE}
        executive = users["m.diallo@beldium.demo"]
        officer = users["a.okafor@beldium.demo"]
        analyst = users["k.adeyemi@beldium.demo"]
        reviewer = users["t.nwosu@regulator.demo"]
        today = timezone.localdate()

        policies = {}
        for title, domain, sector, owner, status, days in [
            ("Responsible Mineral Sourcing Policy", "Supply Chain", "mining", officer, m.PolicyStatus.UNDER_REVIEW, 15),
            ("Anti Bribery and Corruption Standard", "Integrity", "all", executive, m.PolicyStatus.APPROVED, 120),
            ("Export Licensing Control Framework", "Exports", "export", analyst, m.PolicyStatus.DRAFT, 12),
            ("Tailings and Environmental Stewardship", "ESG", "processing", officer, m.PolicyStatus.REVISION_REQUESTED, 4),
            ("Third Party Due Diligence Policy", "Counterparty", "all", analyst, m.PolicyStatus.PUBLISHED, 55),
            ("Warehouse Custody and Chain of Title", "Warehousing", "warehousing", officer, m.PolicyStatus.SUBMITTED, 26),
        ]:
            policy, _ = m.Policy.objects.get_or_create(
                title=title,
                defaults={
                    "domain": domain,
                    "sector": sector,
                    "owner": owner,
                    "created_by": owner,
                    "status": status,
                    "review_date": today + timedelta(days=days),
                    "responsible_organisation": "Beldium Inc.",
                    "jurisdiction": "Nigeria",
                    "executive_summary": f"Sets minimum standards and controls for {domain.lower()} across Beldium sectors.",
                    "version_major": 1 if status == m.PolicyStatus.PUBLISHED else 0,
                    "version_minor": 0 if status == m.PolicyStatus.PUBLISHED else 1,
                },
            )
            if not policy.events.exists():
                m.PolicyEvent.objects.create(policy=policy, action="seed", to_status=policy.status, version=policy.version, actor=owner, comment="Demo record")
            policies[title] = policy

        for title, category, sector, owner, l, i, control, rl, ri, status, days in [
            ("Artisanal site child labour exposure", "governance", "mining", officer, 4, 5, "partial", 3, 4, "escalated", 14),
            ("Export permit fraud via forged certificates", "fraud", "export", analyst, 3, 5, "effective", 2, 4, "monitoring", 29),
            ("Ransomware on custody ledger", "cybersecurity", "warehousing", analyst, 3, 4, "partial", 3, 3, "open", 19),
            ("Tailings dam integrity breach", "environmental", "processing", officer, 2, 5, "effective", 2, 3, "monitoring", 65),
            ("Haulage counterparty insolvency", "counterparty", "logistics", officer, 3, 3, "weak", 3, 3, "open", 9),
            ("Assay result manipulation", "operational", "quality", analyst, 2, 4, "effective", 1, 4, "closed", 77),
            ("New royalty regime price shock", "regulatory", "marketplace", executive, 4, 3, "partial", 4, 2, "open", 34),
            ("Community unrest near concession", "reputational", "mining", officer, 3, 4, "weak", 3, 4, "escalated", 2),
        ]:
            m.Risk.objects.get_or_create(
                title=title,
                defaults={
                    "category": category, "sector": sector, "owner": owner, "created_by": owner,
                    "likelihood": l, "impact": i, "control_effectiveness": control,
                    "residual_likelihood": rl, "residual_impact": ri, "status": status,
                    "due_date": today + timedelta(days=days),
                    "escalated_at": timezone.now() - timedelta(days=days) if status == "escalated" else None,
                    "closed_at": timezone.now() if status == "closed" else None,
                },
            )

        for requirement, authority, gap, status in [
            ("Mining Act s.42: Community Development Agreement", "Ministry of Mines", "CDA not filed for 2 sites", "verified"),
            ("OECD DDG Annex II: Red flag reporting", "OECD (reference)", "Quarterly report late", "pending_verification"),
            ("Export Control Reg. 7(b): Certificate of origin", "Customs Service", "None identified", "verified"),
            ("Data Protection Act: Cross border transfer", "Data Protection Commission", "Conflicts with buyer KYC request", "escalated"),
        ]:
            m.RegulatoryFinding.objects.get_or_create(
                requirement=requirement,
                defaults={
                    "authority": authority, "gap": gap, "status": status, "recorded_by": analyst,
                    "verified_by": reviewer if status == "verified" else None,
                    "verified_at": timezone.now() if status == "verified" else None,
                },
            )

        for name, category, influence, interest, owner in [
            ("Ministry of Mines", "regulator", 9, 8, executive),
            ("Host Community Council", "community", 6, 9, officer),
            ("Customs Service", "regulator", 8, 5, analyst),
            ("Development Finance Partner", "financier", 7, 6, executive),
            ("Offtake Buyers Consortium", "commercial", 5, 7, analyst),
            ("Civil Society Watch", "ngo", 4, 9, officer),
            ("Logistics Operators Association", "industry", 3, 4, officer),
            ("Academic Research Network", "research", 2, 6, analyst),
        ]:
            m.Stakeholder.objects.get_or_create(
                name=name,
                defaults={"category": category, "influence": influence, "interest": interest, "owner": owner},
            )

        start = today.replace(day=1)

        def month(offset):
            year, index = divmod(start.month - 1 + offset, 12)
            return date(start.year + year, index + 1, 1)

        for policy_title, phase, name, owner, s, e, progress, kpi, status in [
            ("Responsible Mineral Sourcing Policy", "design", "Stakeholder consultation", officer, 0, 3, 100, "12 sessions held", "complete"),
            ("Responsible Mineral Sourcing Policy", "pilot", "Mining site pilot (3 sites)", officer, 3, 7, 55, "80% traceability", "on_track"),
            ("Export Licensing Control Framework", "design", "Licensing workflow redesign", analyst, 1, 5, 30, "Cycle time under 10 days", "delayed"),
            ("Tailings and Environmental Stewardship", "rollout", "Tailings monitoring sensors", officer, 4, 10, 15, "100% dams instrumented", "on_track"),
            ("Warehouse Custody and Chain of Title", "rollout", "Custody ledger go live", officer, 6, 11, 0, "Zero title disputes", "not_started"),
        ]:
            m.Milestone.objects.get_or_create(
                name=name,
                defaults={
                    "policy": policies[policy_title], "phase": phase, "owner": owner,
                    "start_date": month(s), "end_date": month(e) - timedelta(days=1),
                    "progress": progress, "kpi": kpi, "status": status,
                },
            )

        assessment, created = m.ImpactAssessment.objects.get_or_create(
            title="Mineral royalty reform",
            defaults={
                "created_by": analyst, "currency": "USD", "unit": "millions", "horizon_years": 10, "discount_rate": 8,
                "assumptions": "Constant 2026 USD; the same 10 year period for all scenarios; commodity price at the 5 year average.",
            },
        )
        if created:
            for position, (name, cost, benefit, verified, baseline, scores) in enumerate([
                ("Baseline (status quo)", 0, 0, True, True, {}),
                ("A: Tiered royalty 3 to 7%", 18.4, 41.2, False, False, {"economic": 3, "social": 2, "environmental": 1, "institutional": 2, "operational": 2, "financial": 3}),
                ("B: Flat royalty 5% plus traceability", 26.1, 48.9, False, False, {"economic": 4, "social": 3, "environmental": 3, "institutional": 3, "operational": 1, "financial": 3}),
                ("C: Phased reform over 3 years", 12.7, 27.5, True, False, {"economic": 2, "social": 2, "environmental": 2, "institutional": 3, "operational": 3, "financial": 2}),
            ]):
                m.ImpactScenario.objects.create(
                    assessment=assessment, name=name, cost=cost, benefit=benefit, verified=verified,
                    is_baseline=baseline, scores=scores, position=position,
                )

        self.stdout.write(self.style.SUCCESS(f"Governance demo data ready. Demo accounts use password {PASSWORD}."))
        for email, _first, _last, role, _org in PEOPLE:
            self.stdout.write(f"  {m.GovernanceRole(role).label}: {email}")
