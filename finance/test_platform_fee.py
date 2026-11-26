from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from ecosystem.models import LifecycleStage, Rfq, Transaction
from finance.models import Invoice, PlatformFeeRate
from mining.models import MineSite
from organisations.models import Organisation


class PlatformFeeTests(TestCase):
    def setUp(self):
        self.seller = Organisation.objects.create(name="Miner", organisation_type="mining_company")
        self.buyer = Organisation.objects.create(name="Buyer", organisation_type="market_participant")
        self.site = MineSite.objects.create(organisation=self.seller, name="Site", mineral="Lithium")
        self.rfq = Rfq.objects.create(
            buyer_organisation=self.buyer, seller_organisation=self.seller,
            mineral="Lithium", quantity_requested=Decimal("10.00"),
        )

    def make_transaction(self, *, invoice=None):
        return Transaction.objects.create(
            rfq=self.rfq, mine_site=self.site, buyer_organisation=self.buyer,
            seller_organisation=self.seller, mineral="Lithium",
            committed_tonnes=Decimal("10.00"), unit_price=Decimal("200.00"),
            finance_invoice=invoice,
        )

    def test_fee_is_fixed_only_at_payment_settlement_from_contract_value(self):
        invoice = Invoice.objects.create(
            seller_organisation=self.seller, buyer_organisation=self.buyer,
            amount=Decimal("2300.00"),
        )
        transaction = self.make_transaction(invoice=invoice)
        self.assertIsNone(transaction.platform_fee_amount)
        self.assertIsNone(invoice.platform_fee_amount)
        transaction.advance_stage(LifecycleStage.PAYMENT_SETTLEMENT)
        invoice.refresh_from_db()
        self.assertEqual(transaction.platform_fee_base_amount, Decimal("2000.00"))
        self.assertEqual(transaction.platform_fee_percent, Decimal("5.0000"))
        self.assertEqual(transaction.platform_fee_amount, Decimal("100.00"))
        self.assertEqual(transaction.platform_fee_payer_id, self.seller.id)
        self.assertIsNotNone(transaction.platform_fee_fixed_at)
        # Invoice mirrors the transaction fee; its own amount is not the base.
        self.assertEqual(invoice.platform_fee_amount, Decimal("100.00"))
        self.assertEqual(invoice.platform_fee_base_amount, Decimal("2000.00"))
        self.assertEqual(invoice.platform_fee_payer_id, self.seller.id)
        self.assertEqual(invoice.platform_fee_rate_id, transaction.platform_fee_rate_id)

    def test_rate_at_settlement_is_snapshotted(self):
        transaction = self.make_transaction()
        PlatformFeeRate.objects.create(
            version=2, rate_percent=Decimal("7.0000"), effective_from=timezone.now(),
        )
        transaction.advance_stage(LifecycleStage.PAYMENT_SETTLEMENT)
        self.assertEqual(transaction.platform_fee_amount, Decimal("140.00"))
        self.assertEqual(transaction.platform_fee_percent, Decimal("7.0000"))
        PlatformFeeRate.objects.create(
            version=3, rate_percent=Decimal("8.0000"), effective_from=timezone.now(),
        )
        transaction.refresh_from_db()
        self.assertEqual(transaction.platform_fee_amount, Decimal("140.00"))
        self.assertEqual(transaction.platform_fee_percent, Decimal("7.0000"))

    def test_contract_value_cannot_change_after_fee_is_fixed(self):
        transaction = self.make_transaction()
        transaction.advance_stage(LifecycleStage.PAYMENT_SETTLEMENT)
        transaction.unit_price = Decimal("250.00")
        with self.assertRaisesRegex(ValueError, "Contract value or fee payer cannot change"):
            transaction.save(update_fields=["unit_price"])
