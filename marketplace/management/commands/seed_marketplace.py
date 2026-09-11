from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from marketplace import services
from marketplace.models import (
    EvidenceStatus,
    ListingStatus,
    MarketplaceAccessGrant,
    MarketplaceDispute,
    MarketplaceLicense,
    MarketplaceOrder,
    MarketplaceProduct,
    OrderStatus,
    PaymentStatus,
    ProductCategory,
    ProductDocument,
    SellerProfile,
    SellerStatus,
)
from organisations.models import MembershipRole, Organisation, OrganisationMembership, OrganisationType


DEMO_PREFIX = "Beldium Marketplace Demo"


def user(email, **extra):
    defaults = {"email_verified_at": timezone.now(), **extra}
    person, created = User.objects.get_or_create(email=email, defaults=defaults)
    if created:
        person.set_password("DemoPass-2026!")
        person.save(update_fields=["password"])
    return person


def pdf(title):
    body = f"BT /F1 12 Tf 72 720 Td (Beldium marketplace demo: {title[:60]}) Tj ET"
    content = (
        b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        + f"4 0 obj << /Length {len(body)} >> stream\n{body}\nendstream endobj\n".encode()
        + b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
    )
    return ContentFile(content, name=f"{title.lower().replace(' ', '-')}.pdf")


class Command(BaseCommand):
    help = "Seed marketplace sellers, listings, evidence, orders, disputes and reports."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true", help="Remove previous marketplace demo data first.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            demo_orgs = Organisation.objects.filter(name__startswith=DEMO_PREFIX)
            SellerProfile.objects.filter(organisation__in=demo_orgs).delete()
            demo_orgs.delete()
            User.objects.filter(email__endswith="@marketplace-demo.test").delete()

        seller_user = user("seller@marketplace-demo.test", first_name="Ada", last_name="Bello")
        reviewer = user("reviewer@marketplace-demo.test", first_name="Musa", last_name="Ibrahim")
        buyer = user("buyer@marketplace-demo.test", first_name="Grace", last_name="Okon")

        rows = [
            ("Ilesa Verified Minerals Ltd", "MKT-RC-1001", ProductCategory.ORE, "Lithium ore", "Osun", ListingStatus.ACTIVE, 90),
            ("Jos Tin Exchange Ltd", "MKT-RC-1002", ProductCategory.CONCENTRATE, "Tin concentrate", "Plateau", ListingStatus.PENDING_REVIEW, 76),
            ("Port Harcourt Equipment Supply", "MKT-RC-1003", ProductCategory.EQUIPMENT, "Conveyor spare parts", "Rivers", ListingStatus.ACTIVE, 86),
        ]
        sellers = []
        for index, (name, rc, category, product_name, state, listing_status, score) in enumerate(rows, start=1):
            org, _ = Organisation.objects.update_or_create(
                registration_number=rc,
                defaults={"name": f"{DEMO_PREFIX}: {name}", "organisation_type": OrganisationType.MINING_COMPANY, "verification_status": "verified", "state": state},
            )
            OrganisationMembership.objects.update_or_create(organisation=org, user=seller_user, defaults={"role": MembershipRole.OWNER, "is_active": True})
            seller, _ = SellerProfile.objects.update_or_create(
                organisation=org,
                defaults={
                    "display_name": name,
                    "contact_name": "Ada Bello",
                    "contact_email": f"seller{index}@marketplace-demo.test",
                    "contact_phone": f"+2348000000{index}",
                    "business_address": f"Industrial Layout, {state}",
                    "service_regions": [state, "Lagos"],
                    "status": SellerStatus.VERIFIED,
                    "trust_score": score,
                    "accepted_terms_at": timezone.now(),
                },
            )
            MarketplaceAccessGrant.objects.update_or_create(seller=seller, user=reviewer, role="reviewer", defaults={"is_active": True})
            product, _ = MarketplaceProduct.objects.update_or_create(
                seller=seller,
                name=product_name,
                defaults={
                    "category": category,
                    "mineral_type": product_name.split()[0],
                    "origin_state": state,
                    "quantity_available": 20 + index * 10,
                    "unit": "tonnes" if category != ProductCategory.EQUIPMENT else "units",
                    "price": 500000 + index * 125000,
                    "status": listing_status,
                },
            )
            if not product.checks.exists():
                services.initialise_product(product)
            product.checks.update(status="passed", score=score, notes="Seeded review.", reviewed_by=reviewer, reviewed_at=timezone.now())
            for doc_type in ["origin_certificate", "trade_license", "assay_report"]:
                ProductDocument.objects.filter(product=product, document_type=doc_type, is_current=True).delete()
                ProductDocument.objects.create(
                    product=product,
                    document_type=doc_type,
                    title=doc_type.replace("_", " ").title(),
                    issuer="Beldium Demo",
                    expires_on=timezone.localdate() + timedelta(days=18 if doc_type == "trade_license" and index == 2 else 365),
                    status=EvidenceStatus.VERIFIED,
                    file=pdf(f"{name} {doc_type}"),
                    original_name=f"{doc_type}.pdf",
                    uploaded_by=seller_user,
                    reviewed_by=reviewer,
                    reviewed_at=timezone.now(),
                )
            services.refresh_product_risk(product)
            MarketplaceLicense.objects.update_or_create(
                seller=seller,
                product=product,
                license_type="Marketplace trading licence",
                defaults={"issuer": "Beldium Demo", "license_number": f"MKT-LIC-{index:03d}", "expires_on": timezone.localdate() + timedelta(days=90), "status": EvidenceStatus.VERIFIED},
            )
            sellers.append((seller, product))

        order, _ = MarketplaceOrder.objects.update_or_create(
            payment_reference="MKT-PAY-DEMO-001",
            defaults={
                "buyer": buyer,
                "seller": sellers[0][0],
                "product": sellers[0][1],
                "quantity": "2.00",
                "unit_price": sellers[0][1].price,
                "currency": sellers[0][1].currency,
                "status": OrderStatus.PAID,
                "payment_status": PaymentStatus.PAID,
                "delivery_address": "Lagos warehouse",
            },
        )
        MarketplaceDispute.objects.get_or_create(order=order, raised_by=buyer, reason="Assay clarification", defaults={"message": "Buyer requested a supporting assay note."})
        services.notify([seller_user, reviewer], "Marketplace demo data ready", "Seeded marketplace sellers and listings are ready.", seller=sellers[0][0])
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(sellers)} marketplace sellers."))
