from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from ecosystem.models import Rfq, Transaction
from finance.models import Invoice, PlatformFeeRate
from mining.models import MineSite
from organisations.models import Organisation


class PlatformFeeTests(TestCase):
    def setUp(self):
        self.seller = Organisation.objects.create(name="Seller", organisation_type="mining_company")
        self.buyer = Organisation.objects.create(name="Buyer", organisation_type="market_participant")
        self.site = MineSite.objects.create(organisation=self.seller, name="Site", mineral="Lithium")
        self.rfq = Rfq.objects.create(
            buyer_organisation=self.buyer, seller_organisation=self.seller,
            mineral="Lithium", quantity_requested=Decimal("10.00"),
        )

    def make_transaction(self):
        return Transaction.objects.create(
            rfq=self.rfq, mine_site=self.site, buyer_organisation=self.buyer,
            seller_organisation=self.seller, mineral="Lithium",
            committed_tonnes=Decimal("10.00"), unit_price=Decimal("200.00"),
        )

    def make_invoice(self):
        return Invoice.objects.create(
            seller_organisation=self.seller, buyer_organisation=self.buyer,
            amount=Decimal("2000.00"),
        )

    def test_five_percent_snapshots_on_transaction_and_invoice(self):
        transaction = self.make_transaction()
        invoice = self.make_invoice()
        self.assertEqual(transaction.platform_fee_percent, Decimal("5.0000"))
        self.assertEqual(transaction.platform_fee_amount, Decimal("100.00"))
        self.assertEqual(invoice.platform_fee_percent, Decimal("5.0000"))
        self.assertEqual(invoice.platform_fee_amount, Decimal("100.00"))
        self.assertEqual(transaction.platform_fee_rate_id, invoice.platform_fee_rate_id)

    def test_new_version_affects_new_records_not_existing_snapshots(self):
        old = self.make_transaction()
        PlatformFeeRate.objects.create(
            version=2, rate_percent=Decimal("7.0000"), effective_from=timezone.now(),
        )
        new = self.make_transaction()
        invoice = self.make_invoice()
        self.assertEqual(old.platform_fee_amount, Decimal("100.00"))
        self.assertEqual(new.platform_fee_amount, Decimal("140.00"))
        self.assertEqual(invoice.platform_fee_amount, Decimal("140.00"))

    def test_amount_edit_recalculates_at_original_rate(self):
        invoice = self.make_invoice()
        invoice.amount = Decimal("1000.00")
        invoice.save(update_fields=["amount"])
        invoice.refresh_from_db()
        self.assertEqual(invoice.platform_fee_amount, Decimal("50.00"))
