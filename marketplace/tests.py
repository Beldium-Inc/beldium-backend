from datetime import timedelta
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from marketplace import services
from marketplace.models import (
    EvidenceStatus,
    ListingStatus,
    MarketplaceAccessGrant,
    MarketplaceDispute,
    MarketplaceOrder,
    MarketplaceProduct,
    OrderStatus,
    PaymentStatus,
    ProductCategory,
    ProductComplianceCheck,
    ProductDocument,
    SellerProfile,
    SellerStatus,
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


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class MarketplaceTests(APITestCase):
    def setUp(self):
        self.owner = make_user("seller@market.test")
        self.reviewer = make_user("reviewer@market.test")
        self.regulator = make_user("regulator@market.test")
        self.buyer = make_user("buyer@market.test")
        self.outsider = make_user("outsider@market.test")
        self.org = make_org("Ilesa Marketplace Seller")
        self.other_org = make_org("Other Marketplace Seller")
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role=MembershipRole.OWNER)
        OrganisationMembership.objects.create(organisation=self.other_org, user=self.outsider, role=MembershipRole.OWNER)
        self.seller = SellerProfile.objects.create(
            organisation=self.org,
            display_name="Ilesa Verified Minerals",
            contact_name="Ada Bello",
            contact_email="seller@market.test",
            contact_phone="+2348000000000",
            service_regions=["Osun", "Lagos"],
            status=SellerStatus.VERIFIED,
            trust_score=84,
        )
        MarketplaceAccessGrant.objects.create(seller=self.seller, user=self.reviewer, role="reviewer")
        MarketplaceAccessGrant.objects.create(seller=self.seller, user=self.regulator, role="regulator")

    def make_product(self, **overrides):
        defaults = {
            "seller": self.seller,
            "name": "Washed lithium ore",
            "category": ProductCategory.ORE,
            "mineral_type": "Lithium",
            "origin_state": "Osun",
            "quantity_available": "25.00",
            "unit": "tonnes",
            "price": "750000.00",
            "status": ListingStatus.DRAFT,
        }
        product = MarketplaceProduct.objects.create(**{**defaults, **overrides})
        services.initialise_product(product)
        return product

    def add_document(self, product, document_type, **overrides):
        defaults = {
            "product": product,
            "document_type": document_type,
            "title": document_type.replace("_", " ").title(),
            "expires_on": timezone.localdate() + timedelta(days=120),
            "status": EvidenceStatus.VERIFIED,
            "file": SimpleUploadedFile(f"{document_type}.pdf", b"%PDF-1.4", content_type="application/pdf"),
            "original_name": f"{document_type}.pdf",
            "uploaded_by": self.owner,
        }
        return ProductDocument.objects.create(**{**defaults, **overrides})

    def complete_product(self, product):
        for document_type in ["origin_certificate", "trade_license", "assay_report"]:
            self.add_document(product, document_type)
        ProductComplianceCheck.objects.filter(product=product).update(
            status="passed",
            score=90,
            notes="Verified.",
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )
        services.refresh_product_risk(product)
        return product

    def test_marketplace_me_uses_membership_and_grants(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("marketplace-me"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["sellers"][0]["can_edit"])
        self.assertFalse(response.data["sellers"][0]["can_review"])

        self.client.force_authenticate(self.reviewer)
        response = self.client.get(reverse("marketplace-me"))
        self.assertTrue(response.data["sellers"][0]["can_review"])
        self.assertFalse(response.data["sellers"][0]["can_edit"])

    def test_seller_user_does_not_see_other_sellers(self):
        SellerProfile.objects.create(
            organisation=self.other_org,
            display_name="Hidden Seller",
            contact_name="Hidden",
            contact_email="hidden@example.test",
            contact_phone="+2348111111111",
        )
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse("marketplace-seller-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_product_submission_requires_evidence_then_reviewer_can_activate(self):
        product = self.make_product()
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("marketplace-product-submit", args=[product.id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "marketplace_listing_incomplete")

        self.complete_product(product)
        response = self.client.post(reverse("marketplace-product-submit", args=[product.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], ListingStatus.PENDING_REVIEW)

        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            reverse("marketplace-product-review", args=[product.id]),
            {"status": "active", "notes": "Ready for market."},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], ListingStatus.ACTIVE)

    def test_document_upload_versions_current_document(self):
        product = self.make_product()
        self.add_document(product, "assay_report")
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("marketplace-product-documents", args=[product.id]),
            {
                "document_type": "assay_report",
                "title": "Updated assay",
                "expires_on": str(timezone.localdate() + timedelta(days=365)),
                "file": SimpleUploadedFile("assay-v2.pdf", b"%PDF-1.4", content_type="application/pdf"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["version"], 2)
        self.assertEqual(ProductDocument.objects.filter(product=product, document_type="assay_report", is_current=True).count(), 1)

    def test_active_listing_can_be_ordered_and_payment_webhook_is_idempotent(self):
        product = self.complete_product(self.make_product(status=ListingStatus.ACTIVE))
        self.client.force_authenticate(self.buyer)
        response = self.client.post(
            reverse("marketplace-order-list"),
            {
                "product": str(product.id),
                "seller": str(self.seller.id),
                "quantity": "2.00",
                "delivery_address": "Lagos warehouse",
                "payment_reference": "PAY-001",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        order = MarketplaceOrder.objects.get(id=response.data["id"])
        webhook_payload = {
            "provider": "demo",
            "event_id": "evt_001",
            "event_type": "payment.success",
            "payment_reference": "PAY-001",
            "status": "paid",
            "payload": {"amount": 1500000},
        }
        response = self.client.post(
            reverse("marketplace-payment-receive"),
            webhook_payload,
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["processed"])
        order.refresh_from_db()
        self.assertEqual(order.payment_status, PaymentStatus.PAID)
        self.assertEqual(order.status, OrderStatus.PAID)
        response = self.client.post(reverse("marketplace-payment-receive"), webhook_payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["processed"])

    def test_seller_can_fulfill_paid_order(self):
        product = self.complete_product(self.make_product(status=ListingStatus.ACTIVE))
        order = MarketplaceOrder.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            product=product,
            quantity="1.00",
            unit_price=product.price,
            currency=product.currency,
            status=OrderStatus.PAID,
            payment_status=PaymentStatus.PAID,
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            reverse("marketplace-order-fulfill", args=[order.id]),
            {"shipping_reference": "SHIP-001"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], OrderStatus.SHIPPED)

    def test_dispute_resolution_requires_reviewer(self):
        product = self.complete_product(self.make_product(status=ListingStatus.ACTIVE))
        order = MarketplaceOrder.objects.create(
            buyer=self.buyer,
            seller=self.seller,
            product=product,
            quantity="1.00",
            unit_price=product.price,
            currency=product.currency,
            status=OrderStatus.PAID,
            payment_status=PaymentStatus.PAID,
        )
        dispute = MarketplaceDispute.objects.create(order=order, raised_by=self.buyer, reason="Quality", message="Assay mismatch.")
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("marketplace-dispute-resolve", args=[dispute.id]), {"resolution": "Refund approved."}, format="json")
        self.assertEqual(response.status_code, 403)
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(reverse("marketplace-dispute-resolve", args=[dispute.id]), {"resolution": "Refund approved."}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "resolved")

    def test_dashboard_risk_notifications_and_reports_exist(self):
        product = self.complete_product(self.make_product(status=ListingStatus.ACTIVE))
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(reverse("marketplace-dashboard")).status_code, 200)
        response = self.client.get(reverse("marketplace-risk"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["products"][0]["product_id"], product.id)
        report = self.client.post(reverse("marketplace-report-list"))
        self.assertEqual(report.status_code, 201)
        self.assertEqual(self.client.get(reverse("marketplace-report-download", args=[report.data["id"]])).status_code, 200)
