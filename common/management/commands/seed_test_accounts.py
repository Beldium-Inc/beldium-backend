"""Create a full matrix of known-password test logins, one per (vertical x role).

The platform has no per-vertical role field on ``accounts.User`` — access is
derived at request time from active ``organisations.OrganisationMembership``
rows (applicant/company-side) and per-vertical ``*AccessGrant`` rows
(reviewer/regulator desk-side, on export/warehousing/marketplace/logistics).
See ``organisations/access.py`` (processing, mining, quality) and each
vertical's ``permissions.py`` (export, warehousing, marketplace, logistics)
for the exact resolution rules this command targets.

Every user gets the same password (see PASSWORD below) so a human can log
into the running frontend and click through each role. Existing users and
their passwords are never touched — this only creates/updates the
``@beldium.test`` accounts it owns.

Idempotent: everything is keyed by email (users), by name/registration
number (organisations), and by unique-together constraints (memberships,
access grants) via get_or_create/update_or_create, so re-running refreshes
rather than duplicates.

Run after `seed_processing`, `seed_marketplace` and `seed_logistics` so the
applicant-side accounts here can be attached to those commands' demo
organisations and see real records rather than an empty register.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from export.models import Exporter, ExportAccessGrant
from logistics.models import LogisticsCompany, LogisticsAccessGrant
from marketplace.models import SellerProfile, MarketplaceAccessGrant
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType
from warehousing.models import WarehouseOperator, WarehousingAccessGrant

PASSWORD = "Beldium2026!"

# Names used by the existing seed commands, so we can attach applicant-side
# test users to real demo data instead of an empty register.
PROCESSING_DEMO_ORG = "Beldium Processing Demo Register"
MARKETPLACE_DEMO_ORG_PREFIX = "Beldium Marketplace Demo: Ilesa Verified Minerals Ltd"
LOGISTICS_DEMO_ORG_PREFIX = "Beldium Logistics Demo: Ilesa Heavy Haulage Ltd"


class Command(BaseCommand):
    help = "Seed a full matrix of known-password test users covering every vertical x role."

    @transaction.atomic
    def handle(self, *args, **options):
        self.rows = []  # (vertical, role, email, description) for the summary table

        self.seed_processing()
        self.seed_mining()
        self.seed_quality()
        self.seed_export()
        self.seed_warehousing()
        self.seed_marketplace()
        self.seed_logistics()

        self.stdout.write(self.style.SUCCESS(f"Seeded/updated {len(self.rows)} test accounts. Password for all: {PASSWORD}"))
        for vertical, role, email, desc in self.rows:
            self.stdout.write(f"  {vertical:12s} {role:10s} {email:38s} {desc}")

    # -- helpers ------------------------------------------------------------

    def user(self, email, first_name, last_name, is_staff=False):
        person, _ = User.objects.get_or_create(
            email=email,
            defaults={"first_name": first_name, "last_name": last_name, "is_active": True, "email_verified_at": timezone.now()},
        )
        person.first_name = first_name
        person.last_name = last_name
        person.is_active = True
        person.is_staff = is_staff
        person.set_password(PASSWORD)
        person.save()
        return person

    def org(self, name, org_type, registration_number=""):
        defaults = {"organisation_type": org_type, "verification_status": "verified", "country": "Nigeria"}
        if registration_number:
            organisation, _ = Organisation.objects.update_or_create(registration_number=registration_number, defaults={"name": name, **defaults})
        else:
            organisation, _ = Organisation.objects.get_or_create(name=name, defaults=defaults)
            organisation.organisation_type = org_type
            organisation.verification_status = "verified"
            organisation.save()
        return organisation

    def membership(self, organisation, person, role):
        OrganisationMembership.objects.update_or_create(
            organisation=organisation, user=person, defaults={"role": role, "is_active": True}
        )

    def note(self, vertical, role, email, desc):
        self.rows.append((vertical, role, email, desc))

    # -- processing / mining (organisations.access three-audience model) ----

    def seed_processing(self):
        operator_org = self.org("Beldium Processing Demo Reviewer Partner", OrganisationType.COMPLIANCE_PARTNER)
        regulator_org = self.org("Beldium Processing Demo Regulator", OrganisationType.REGULATOR)

        operator = self.user("processing.operator@beldium.test", "Chidi", "Operator")
        self.membership(operator_org, operator, MembershipRole.ADMIN)
        self.note("processing", "operator", operator.email, "Compliance desk: review sections, raise findings, decide applications.")

        regulator = self.user("processing.regulator@beldium.test", "Ngozi", "Regulator")
        self.membership(regulator_org, regulator, MembershipRole.MEMBER)
        self.note("processing", "regulator", regulator.email, "Read-only oversight of the whole processing register.")

        applicant = self.user("processing.applicant@beldium.test", "Tunde", "Applicant")
        demo_org, created = Organisation.objects.get_or_create(
            name=PROCESSING_DEMO_ORG,
            defaults={"organisation_type": OrganisationType.MINING_COMPANY, "verification_status": "verified", "country": "Nigeria"},
        )
        self.membership(demo_org, applicant, MembershipRole.ADMIN)
        self.note("processing", "processor", applicant.email, "Applicant/registrant: sees only its own processors/applications (run seed_processing first for real data).")

    def seed_mining(self):
        operator_org = self.org("Beldium Mining Demo Reviewer Partner", OrganisationType.COMPLIANCE_PARTNER)
        regulator_org = self.org("Beldium Mining Demo Regulator", OrganisationType.REGULATOR)
        applicant_org = self.org("Beldium Mining Demo Co", OrganisationType.MINING_COMPANY)

        operator = self.user("mining.operator@beldium.test", "Amaka", "Operator")
        self.membership(operator_org, operator, MembershipRole.ADMIN)
        self.note("mining", "operator", operator.email, "Compliance desk: review, decide mining applications.")

        regulator = self.user("mining.regulator@beldium.test", "Emeka", "Regulator")
        self.membership(regulator_org, regulator, MembershipRole.MEMBER)
        self.note("mining", "regulator", regulator.email, "Read-only oversight of the whole mining register.")

        applicant = self.user("mining.applicant@beldium.test", "Fatima", "Applicant")
        self.membership(applicant_org, applicant, MembershipRole.ADMIN)
        self.note("mining", "miner", applicant.email, "Mining company applicant: own site/application records only.")

    def seed_quality(self):
        # quality.services.derive_role: is_staff -> operator; mining_company -> miner;
        # compliance_partner/laboratory/inspection_body -> partner; regulator -> regulator.
        operator = self.user("quality.operator@beldium.test", "Bisi", "Operator", is_staff=True)
        self.note("quality", "operator", operator.email, "Internal quality officer (is_staff): full review/decide across the register.")

        partner_org = self.org("Beldium Quality Demo Partner", OrganisationType.COMPLIANCE_PARTNER)
        partner = self.user("quality.partner@beldium.test", "Kunle", "Partner")
        self.membership(partner_org, partner, MembershipRole.ADMIN)
        self.note("quality", "partner", partner.email, "External compliance/lab partner reviewer.")

        miner_org = self.org("Beldium Quality Demo Miner", OrganisationType.MINING_COMPANY)
        miner = self.user("quality.miner@beldium.test", "Yemisi", "Miner")
        self.membership(miner_org, miner, MembershipRole.ADMIN)
        self.note("quality", "miner", miner.email, "Mining company applicant for quality certification.")

        regulator_org = self.org("Beldium Quality Demo Regulator", OrganisationType.REGULATOR)
        regulator = self.user("quality.regulator@beldium.test", "Ibrahim", "Regulator")
        self.membership(regulator_org, regulator, MembershipRole.MEMBER)
        self.note("quality", "regulator", regulator.email, "Read-only oversight of the quality register.")

    # -- export / warehousing / marketplace / logistics (grant-based desk) --

    def seed_export(self):
        applicant_org = self.org("Beldium Export Demo Ltd", OrganisationType.MINING_COMPANY, registration_number="EXP-RC-900001")
        applicant = self.user("export.applicant@beldium.test", "Chioma", "Exporter")
        self.membership(applicant_org, applicant, MembershipRole.OWNER)
        exporter, _ = Exporter.objects.update_or_create(
            organisation=applicant_org,
            defaults={"contact_name": "Chioma Exporter", "contact_email": applicant.email, "contact_phone": "+2348010000001"},
        )
        self.note("export", "applicant", applicant.email, "Exporter company owner: manages its own exporter profile, shipments, documents.")

        reviewer = self.user("export.reviewer@beldium.test", "Segun", "Reviewer")
        ExportAccessGrant.objects.update_or_create(exporter=exporter, user=reviewer, role="reviewer", defaults={"is_active": True})
        self.note("export", "reviewer", reviewer.email, "Internal desk reviewer for Beldium Export Demo Ltd: reviews/decides its applications.")

        regulator = self.user("export.regulator@beldium.test", "Halima", "Regulator")
        ExportAccessGrant.objects.update_or_create(exporter=exporter, user=regulator, role="regulator", defaults={"is_active": True})
        self.note("export", "regulator", regulator.email, "Read-only oversight grant on Beldium Export Demo Ltd.")

    def seed_warehousing(self):
        applicant_org = self.org("Beldium Warehousing Demo Ltd", OrganisationType.MINING_COMPANY, registration_number="WRH-RC-900001")
        applicant = self.user("warehousing.operator@beldium.test", "Uche", "Warehouseman")
        self.membership(applicant_org, applicant, MembershipRole.OWNER)
        warehouse, _ = WarehouseOperator.objects.update_or_create(
            organisation=applicant_org,
            defaults={"contact_name": "Uche Warehouseman", "contact_email": applicant.email, "contact_phone": "+2348010000002"},
        )
        self.note("warehousing", "operator", applicant.email, "Warehouse operator company owner: manages its own facility/inventory/incidents.")

        partner = self.user("warehousing.partner@beldium.test", "Zainab", "Partner")
        WarehousingAccessGrant.objects.update_or_create(warehouse=warehouse, user=partner, role="reviewer", defaults={"is_active": True})
        self.note("warehousing", "partner", partner.email, "External compliance-partner reviewer for Beldium Warehousing Demo Ltd.")

        regulator = self.user("warehousing.regulator@beldium.test", "Peter", "Regulator")
        WarehousingAccessGrant.objects.update_or_create(warehouse=warehouse, user=regulator, role="regulator", defaults={"is_active": True})
        self.note("warehousing", "regulator", regulator.email, "Read-only oversight grant on Beldium Warehousing Demo Ltd.")

    def seed_marketplace(self):
        applicant = self.user("marketplace.seller@beldium.test", "Ada", "Seller")
        seller = SellerProfile.objects.filter(organisation__name__startswith=MARKETPLACE_DEMO_ORG_PREFIX).first()
        if seller is None:
            org = self.org("Beldium Marketplace Demo: Test Seller Co", OrganisationType.MINING_COMPANY, registration_number="MKT-RC-900001")
            seller, _ = SellerProfile.objects.update_or_create(
                organisation=org,
                defaults={
                    "display_name": "Beldium Marketplace Demo: Test Seller Co",
                    "contact_name": "Ada Seller",
                    "contact_email": applicant.email,
                    "contact_phone": "+2348010000003",
                    "status": "verified",
                    "trust_score": 80,
                },
            )
        self.membership(seller.organisation, applicant, MembershipRole.OWNER)
        self.note("marketplace", "seller", applicant.email, "Seller company owner (run seed_marketplace first for full listings/orders data).")

        reviewer = self.user("marketplace.reviewer@beldium.test", "Musa", "Reviewer")
        MarketplaceAccessGrant.objects.update_or_create(seller=seller, user=reviewer, role="reviewer", defaults={"is_active": True})
        self.note("marketplace", "reviewer", reviewer.email, "Internal desk reviewer for the demo seller.")

        regulator = self.user("marketplace.regulator@beldium.test", "Grace", "Regulator")
        MarketplaceAccessGrant.objects.update_or_create(seller=seller, user=regulator, role="regulator", defaults={"is_active": True})
        self.note("marketplace", "regulator", regulator.email, "Read-only oversight grant on the demo seller.")

    def seed_logistics(self):
        applicant = self.user("logistics.company@beldium.test", "Kayode", "Haulier")
        company = LogisticsCompany.objects.filter(organisation__name__startswith=LOGISTICS_DEMO_ORG_PREFIX).first()
        if company is None:
            org = self.org("Beldium Logistics Demo: Test Haulage Co", OrganisationType.MINING_COMPANY, registration_number="LOG-RC-900001")
            company, _ = LogisticsCompany.objects.update_or_create(
                organisation=org,
                defaults={"contact_name": "Kayode Haulier", "contact_email": applicant.email, "contact_phone": "+2348010000004"},
            )
        self.membership(company.organisation, applicant, MembershipRole.OWNER)
        self.note("logistics", "company", applicant.email, "Logistics company owner (run seed_logistics first for full fleet/shipment data).")

        reviewer = self.user("logistics.reviewer@beldium.test", "Ijeoma", "Reviewer")
        LogisticsAccessGrant.objects.update_or_create(company=company, user=reviewer, role="reviewer", defaults={"is_active": True})
        self.note("logistics", "reviewer", reviewer.email, "Internal desk reviewer for the demo logistics company.")

        regulator = self.user("logistics.regulator@beldium.test", "Bola", "Regulator")
        LogisticsAccessGrant.objects.update_or_create(company=company, user=regulator, role="regulator", defaults={"is_active": True})
        self.note("logistics", "regulator", regulator.email, "Read-only oversight grant on the demo logistics company.")
