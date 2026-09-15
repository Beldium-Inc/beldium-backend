"""Seed one believable end-to-end cross-domain example.

One RFQ -> accepted -> a Transaction moving through several stages, with real
linked Sample / InventoryLot / TraceabilityRun / Shipment / Invoice records
wherever the stage has reached that point — mirroring the four illustrative
transactions in the Miner Hub prototype's ``ecosystem-data.ts`` seed
(in_transit, processing_started, payment_settlement, sample_collected), tied
to the same ``Beldium Mining Demo Co`` / ``Ijero Lithium Pit A`` site that
``seed_test_accounts`` gives ``mining.applicant@beldium.test``.

Idempotent: everything is keyed by demo org name / reference, via
get_or_create / update_or_create, so re-running refreshes rather than
duplicates.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from django.utils import timezone

from ecosystem.models import (
    BatchStage,
    Commitment,
    LifecycleStage,
    LogisticsMove,
    MaterialBatch,
    MoveKind,
    MoveStatus,
    Rfq,
    RfqStatus,
    Transaction,
)
from export.models import Buyer as ExportBuyer, Exporter, Product as ExportProduct, Shipment
from finance.models import Invoice, InvoiceStatus, Payment, PaymentStatus
from mining.models import MineSite
from organisations.models import Organisation, OrganisationType
from processing.models import Processor, ProcessorStatus, ProcessingType, TraceabilityRun
from quality.models import Sample, SampleStatus
from warehousing.models import Facility as WarehouseFacility, InventoryLot, StorageZone, WarehouseOperator

MINING_DEMO_ORG = "Beldium Mining Demo Co"
SITE_NAME = "Ijero Lithium Pit A"
BUYER_ORG = "Beldium Ecosystem Demo Buyer: Tianhe Smelting Group"


class Command(BaseCommand):
    help = "Seed the ecosystem/finance apps with one believable end-to-end cross-domain example."

    @db_transaction.atomic
    def handle(self, *args, **options):
        try:
            mining_org = Organisation.objects.get(name=MINING_DEMO_ORG)
        except Organisation.DoesNotExist:
            raise CommandError(
                f"Organisation '{MINING_DEMO_ORG}' not found. Run `manage.py seed_test_accounts` first."
            )
        try:
            site = MineSite.objects.get(organisation=mining_org, name=SITE_NAME)
        except MineSite.DoesNotExist:
            raise CommandError(
                f"Mine site '{SITE_NAME}' not found. Run `manage.py seed_mining` first."
            )

        buyer_org, _ = Organisation.objects.get_or_create(
            name=BUYER_ORG,
            defaults={
                "organisation_type": OrganisationType.MINING_COMPANY,
                "verification_status": "verified",
                "country": "China",
            },
        )

        now = timezone.now()

        rfq, _ = Rfq.objects.update_or_create(
            reference="RFQ-2026-0198",
            defaults=dict(
                buyer_organisation=buyer_org,
                seller_organisation=mining_org,
                mineral="Lithium concentrate",
                grade_spec="≥ 22% Li2O",
                quantity_requested=8000,
                unit="t",
                indicative_price=398,
                currency="USD",
                incoterm="FOB Lagos",
                destination="Qingdao",
                received_at=now - timedelta(days=24),
                respond_by=now - timedelta(days=18),
                notes="Accepted in part — capacity limited by plant throughput.",
                status=RfqStatus.PARTIALLY_ACCEPTED,
                committed_quantity=4500,
                decided_at=now - timedelta(days=23),
            ),
        )

        # -- a second, still-open RFQ so the dashboard's "active RFQs" isn't a
        # single-row degenerate case.
        Rfq.objects.update_or_create(
            reference="RFQ-2026-0203",
            defaults=dict(
                buyer_organisation=buyer_org,
                seller_organisation=mining_org,
                mineral="Cobalt hydroxide",
                grade_spec="≥ 28% Co",
                quantity_requested=800,
                unit="t",
                indicative_price=5400,
                currency="USD",
                incoterm="FOB Lagos",
                destination="Kotka",
                received_at=now - timedelta(days=2),
                respond_by=now + timedelta(days=6),
                notes="ESG documentation pack required with acceptance.",
                status=RfqStatus.OPEN,
                committed_quantity=0,
            ),
        )

        tx, _ = Transaction.objects.update_or_create(
            reference="TX-2026-4462",
            defaults=dict(
                rfq=rfq,
                mine_site=site,
                buyer_organisation=buyer_org,
                seller_organisation=mining_org,
                mineral="Lithium concentrate",
                grade_spec="≥ 22% Li2O",
                committed_tonnes=500,
                aggregated_tonnes=500,
                unit_price=5600,
                currency="USD",
                incoterm="FOB Lagos",
                destination="Izmir",
                stage=LifecycleStage.RFQ_RECEIVED,
            ),
        )
        Commitment.objects.get_or_create(transaction=tx, mine_site=site)

        # -- quality: full sample lifecycle, accepted.
        sample, _ = Sample.objects.update_or_create(
            reference="SMP-4462-A",
            defaults=dict(
                material="Lithium concentrate",
                lot="LOT-4462",
                mine_site=site.name,
                origin=site.state,
                mass_kg=20,
                registered_at=now - timedelta(days=20),
                miner_org=mining_org.name,
                buyer_org=buyer_org.name,
                status=SampleStatus.CERTIFIED,
                results=[{"grade": 31.2, "moisture": 3.8, "published_at": (now - timedelta(days=13)).isoformat()}],
            ),
        )

        # -- warehousing: a received, stored lot.
        warehouse_org, _ = Organisation.objects.get_or_create(
            name="Beldium Ecosystem Demo Warehouse Operator",
            defaults={"organisation_type": OrganisationType.MINING_COMPANY, "verification_status": "verified"},
        )
        wh_operator, _ = WarehouseOperator.objects.get_or_create(
            organisation=warehouse_org,
            defaults=dict(contact_name="Warehouse Ops", contact_email="ops@beldium-wh.test", contact_phone="+2340000000"),
        )
        wh_facility, _ = WarehouseFacility.objects.get_or_create(
            warehouse=wh_operator,
            name="Kitwe Bonded Warehouse",
            defaults=dict(facility_type="bonded", address="Kitwe", state="Copperbelt", capacity=10000),
        )
        wh_zone, _ = StorageZone.objects.get_or_create(
            facility=wh_facility,
            name="Bay 2",
            defaults=dict(warehouse=wh_operator, storage_type="bulk", capacity=5000),
        )
        lot, _ = InventoryLot.objects.update_or_create(
            reference="LOT-4462",
            defaults=dict(
                warehouse=wh_operator,
                facility=wh_facility,
                zone=wh_zone,
                product_name="Lithium concentrate",
                batch_number="BATCH-4462",
                owner_name=mining_org.name,
                quantity=500,
                actual_weighbridge_quantity=499,
                received_on=(now - timedelta(days=17)).date(),
                status="dispatched",
            ),
        )

        # -- processing: a completed traceability run.
        processor, _ = Processor.objects.get_or_create(
            name="Kitwe Hydromet Plant",
            defaults=dict(organisation=warehouse_org, processing_type=ProcessingType.CHEMICAL_REFINING, status=ProcessorStatus.APPROVED, state="Copperbelt"),
        )
        run, _ = TraceabilityRun.objects.update_or_create(
            reference="BPC-RUN-4462",
            defaults=dict(
                processor=processor,
                facility_name="Kitwe Hydromet Plant",
                input_batch="BATCH-4462",
                input_source=site.name,
                input_mass_kg=499_000,
                process="Leach + precipitation",
                started_at=now - timedelta(days=16),
                completed_at=now - timedelta(days=10),
                output_batch="OUT-4462",
                output_mass_kg=486_000,
                qc_assay="30.8%",
                qc_verdict=TraceabilityRun.Verdict.PASS,
            ),
        )

        # -- export: a delivered shipment.
        exporter_org, _ = Organisation.objects.get_or_create(
            name="Beldium Ecosystem Demo Exporter",
            defaults={"organisation_type": OrganisationType.MINING_COMPANY, "verification_status": "verified"},
        )
        exporter, _ = Exporter.objects.get_or_create(
            organisation=exporter_org,
            defaults=dict(contact_name="Export Desk", contact_email="export@beldium-exp.test", contact_phone="+2340000001"),
        )
        export_product, _ = ExportProduct.objects.get_or_create(
            exporter=exporter, name="Lithium concentrate", hs_code="2530.90", defaults=dict(unit="tonnes")
        )
        export_buyer, _ = ExportBuyer.objects.get_or_create(
            exporter=exporter, name=buyer_org.name, country="Türkiye", defaults=dict(screening_status="cleared")
        )
        shipment, _ = Shipment.objects.update_or_create(
            reference="SHP-2026-4462",
            defaults=dict(
                exporter=exporter,
                product=export_product,
                buyer=export_buyer,
                destination_country="Türkiye",
                port_of_loading="Lagos",
                port_of_discharge="Izmir",
                quantity=500,
                estimated_value=500 * 5600,
                expected_ship_date=(now - timedelta(days=3)).date(),
                status="shipped",
                decision_outcome="cleared",
                decision_at=now - timedelta(days=4),
            ),
        )

        # -- finance: an issued, fully paid invoice.
        invoice, _ = Invoice.objects.update_or_create(
            reference="INV-4462",
            defaults=dict(
                seller_organisation=mining_org,
                buyer_organisation=buyer_org,
                transaction_reference=tx.reference,
                description="Lithium concentrate 500t — TX-2026-4462",
                amount=500 * 5600,
                advance_percent=30,
                status=InvoiceStatus.ISSUED,
                issued_at=now - timedelta(days=15),
                due_at=now - timedelta(days=2),
            ),
        )
        Payment.objects.update_or_create(
            reference="PMT-4462-A",
            defaults=dict(
                invoice=invoice,
                amount=500 * 5600,
                method="bank_transfer",
                status=PaymentStatus.CLEARED,
                received_at=now - timedelta(days=1),
            ),
        )
        invoice.refresh_from_db()
        invoice.refresh_status()

        tx.quality_sample = sample
        tx.warehousing_lot = lot
        tx.processing_run = run
        tx.export_shipment = shipment
        tx.finance_invoice = invoice
        tx.save()

        # -- stage log: walk the full lifecycle so the timeline/activity feed
        # has real, chronologically ordered events.
        stage_times = {
            LifecycleStage.RFQ_RECEIVED: now - timedelta(days=24),
            LifecycleStage.ACCEPTED: now - timedelta(days=23),
            LifecycleStage.AGGREGATION: now - timedelta(days=22),
            LifecycleStage.SAMPLE_REQUESTED: now - timedelta(days=21),
            LifecycleStage.SAMPLE_LOGISTICS: now - timedelta(days=21),
            LifecycleStage.SAMPLE_COLLECTED: now - timedelta(days=20),
            LifecycleStage.LAB_RECEIVED: now - timedelta(days=19),
            LifecycleStage.TESTING: now - timedelta(days=19),
            LifecycleStage.RESULTS_PUBLISHED: now - timedelta(days=16),
            LifecycleStage.BUYER_QUALITY_ACCEPTANCE: now - timedelta(days=15),
            LifecycleStage.BULK_LOGISTICS: now - timedelta(days=14),
            LifecycleStage.MATERIAL_PICKED_UP: now - timedelta(days=13),
            LifecycleStage.IN_TRANSIT: now - timedelta(days=13),
            LifecycleStage.WAREHOUSE_RECEIVED: now - timedelta(days=17),
            LifecycleStage.PROCESSING_STARTED: now - timedelta(days=16),
            LifecycleStage.PROCESSING_COMPLETED: now - timedelta(days=10),
            LifecycleStage.OUTPUT_RECORDED: now - timedelta(days=9),
            LifecycleStage.POST_PROCESSING_QUALITY: now - timedelta(days=8),
            LifecycleStage.EXPORT_COMPLIANCE: now - timedelta(days=6),
            LifecycleStage.EXPORT_READY: now - timedelta(days=5),
            LifecycleStage.SHIPPED: now - timedelta(days=3),
            LifecycleStage.BUYER_DESTINATION: now - timedelta(days=1, hours=6),
            LifecycleStage.DELIVERED: now - timedelta(days=1),
            LifecycleStage.PAYMENT_SETTLEMENT: now - timedelta(hours=6),
        }
        # advance_stage() forbids going backwards from whatever the current
        # stage already is (idempotent re-run), so walk it forward in order.
        for stage, at in stage_times.items():
            if stage_order(stage) < stage_order(tx.stage):
                continue
            tx.stage_events.filter(stage=stage).delete()
            tx.advance_stage(stage, note=STAGE_NOTES.get(stage, ""), occurred_at=at)
        tx.refresh_from_db()

        # -- material batches: the delivered one for tx, plus loose stockpile.
        MaterialBatch.objects.update_or_create(
            reference="BATCH-LI-4462",
            defaults=dict(
                mine_site=site,
                mineral="Lithium concentrate",
                tonnes=500,
                grade=31.2,
                stage=BatchStage.DELIVERED,
                location="Izmir, delivered to buyer",
                transaction=tx,
            ),
        )
        MaterialBatch.objects.update_or_create(
            reference="BATCH-LI-STOCK",
            defaults=dict(
                mine_site=site,
                mineral="Lithium concentrate",
                tonnes=3400,
                grade=22.4,
                stage=BatchStage.STOCKPILE,
                location=f"{site.name} — pad 1",
                transaction=None,
            ),
        )

        LogisticsMove.objects.update_or_create(
            reference="MV-4462-S",
            defaults=dict(
                transaction=tx,
                kind=MoveKind.SAMPLE,
                carrier_name="SwiftLab Couriers",
                vehicle="LC-3301",
                driver="R. Zulu",
                from_location=site.name,
                to_location="Kitwe Metallurgical Institute",
                tonnes="0.020",
                status=MoveStatus.DELIVERED,
                assigned_at=now - timedelta(days=21),
                picked_up_at=now - timedelta(days=20),
                arrived_at=now - timedelta(days=19),
            ),
        )
        LogisticsMove.objects.update_or_create(
            reference="MV-4462-B",
            defaults=dict(
                transaction=tx,
                kind=MoveKind.BULK,
                carrier_name="Beira Corridor Freight",
                vehicle="Convoy BC-22",
                driver="S. Nyoni (lead)",
                from_location=site.name,
                to_location="Kitwe Hydromet Plant",
                tonnes=500,
                status=MoveStatus.DELIVERED,
                assigned_at=now - timedelta(days=14),
                picked_up_at=now - timedelta(days=13),
                arrived_at=now - timedelta(days=10),
            ),
        )

        # -- a richer portfolio: more open/accepted RFQs and transactions
        # spanning every mid-pipeline stage, so dashboard aggregates (active
        # RFQs, in-transit/in-processing/export-ready tonnage, outstanding
        # payments, available inventory) are non-zero and believable, not
        # dominated by the single fully-settled TX-2026-4462 above.

        Rfq.objects.update_or_create(
            reference="RFQ-2026-0211",
            defaults=dict(
                buyer_organisation=buyer_org,
                seller_organisation=mining_org,
                mineral="Lithium concentrate",
                grade_spec="≥ 20% Li2O",
                quantity_requested=1200,
                unit="t",
                indicative_price=380,
                currency="USD",
                incoterm="FOB Lagos",
                destination="Busan",
                received_at=now - timedelta(days=5),
                respond_by=now + timedelta(days=3),
                notes="Spot cargo, buyer flexible on grade within spec band.",
                status=RfqStatus.OPEN,
                committed_quantity=0,
            ),
        )
        rfq_accepted, _ = Rfq.objects.update_or_create(
            reference="RFQ-2026-0215",
            defaults=dict(
                buyer_organisation=buyer_org,
                seller_organisation=mining_org,
                mineral="Lithium concentrate",
                grade_spec="≥ 24% Li2O",
                quantity_requested=650,
                unit="t",
                indicative_price=410,
                currency="USD",
                incoterm="FOB Lagos",
                destination="Qingdao",
                received_at=now - timedelta(days=9),
                respond_by=now - timedelta(days=3),
                notes="Accepted; awaiting aggregation before sampling.",
                status=RfqStatus.ACCEPTED,
                committed_quantity=650,
                decided_at=now - timedelta(days=8),
            ),
        )

        def walk_stages(txn, ordered_stage_times):
            for stage, at in ordered_stage_times.items():
                if stage_order(stage) < stage_order(txn.stage):
                    continue
                txn.stage_events.filter(stage=stage).delete()
                txn.advance_stage(stage, occurred_at=at)
            txn.refresh_from_db()

        # -- TX in transit: bulk logistics under way, not yet warehoused.
        tx_transit, _ = Transaction.objects.update_or_create(
            reference="TX-2026-4470",
            defaults=dict(
                rfq=rfq_accepted,
                mine_site=site,
                buyer_organisation=buyer_org,
                seller_organisation=mining_org,
                mineral="Lithium concentrate",
                grade_spec="≥ 24% Li2O",
                committed_tonnes=650,
                aggregated_tonnes=650,
                unit_price=5750,
                currency="USD",
                incoterm="FOB Lagos",
                destination="Qingdao",
                stage=LifecycleStage.RFQ_RECEIVED,
            ),
        )
        Commitment.objects.get_or_create(transaction=tx_transit, mine_site=site)
        walk_stages(tx_transit, {
            LifecycleStage.RFQ_RECEIVED: now - timedelta(days=9),
            LifecycleStage.ACCEPTED: now - timedelta(days=8),
            LifecycleStage.AGGREGATION: now - timedelta(days=7),
            LifecycleStage.SAMPLE_REQUESTED: now - timedelta(days=6),
            LifecycleStage.SAMPLE_COLLECTED: now - timedelta(days=5),
            LifecycleStage.RESULTS_PUBLISHED: now - timedelta(days=4),
            LifecycleStage.BUYER_QUALITY_ACCEPTANCE: now - timedelta(days=3),
            LifecycleStage.BULK_LOGISTICS: now - timedelta(days=2),
            LifecycleStage.MATERIAL_PICKED_UP: now - timedelta(days=1, hours=12),
            LifecycleStage.IN_TRANSIT: now - timedelta(hours=18),
        })
        MaterialBatch.objects.update_or_create(
            reference="BATCH-LI-4470",
            defaults=dict(
                mine_site=site, mineral="Lithium concentrate", tonnes=650, grade=24.6,
                stage=BatchStage.IN_TRANSIT, location="En route to Lagos port", transaction=tx_transit,
            ),
        )
        LogisticsMove.objects.update_or_create(
            reference="MV-4470-B",
            defaults=dict(
                transaction=tx_transit, kind=MoveKind.BULK, carrier_name="Beira Corridor Freight",
                vehicle="Convoy BC-31", driver="T. Mensah", from_location=site.name, to_location="Lagos Port",
                tonnes=650, status=MoveStatus.IN_TRANSIT, assigned_at=now - timedelta(days=2),
                picked_up_at=now - timedelta(days=1, hours=12), arrived_at=None,
            ),
        )

        # -- TX in processing: warehoused and processing started.
        tx_processing, _ = Transaction.objects.update_or_create(
            reference="TX-2026-4475",
            defaults=dict(
                rfq=rfq, mine_site=site, buyer_organisation=buyer_org, seller_organisation=mining_org,
                mineral="Lithium concentrate", grade_spec="≥ 22% Li2O", committed_tonnes=900,
                aggregated_tonnes=900, unit_price=5680, currency="USD", incoterm="FOB Lagos",
                destination="Kotka", stage=LifecycleStage.RFQ_RECEIVED,
            ),
        )
        Commitment.objects.get_or_create(transaction=tx_processing, mine_site=site)
        walk_stages(tx_processing, {
            LifecycleStage.RFQ_RECEIVED: now - timedelta(days=18),
            LifecycleStage.ACCEPTED: now - timedelta(days=17),
            LifecycleStage.AGGREGATION: now - timedelta(days=16),
            LifecycleStage.SAMPLE_COLLECTED: now - timedelta(days=14),
            LifecycleStage.RESULTS_PUBLISHED: now - timedelta(days=12),
            LifecycleStage.BUYER_QUALITY_ACCEPTANCE: now - timedelta(days=11),
            LifecycleStage.BULK_LOGISTICS: now - timedelta(days=10),
            LifecycleStage.MATERIAL_PICKED_UP: now - timedelta(days=9),
            LifecycleStage.IN_TRANSIT: now - timedelta(days=8),
            LifecycleStage.WAREHOUSE_RECEIVED: now - timedelta(days=6),
            LifecycleStage.PROCESSING_STARTED: now - timedelta(days=4),
        })
        MaterialBatch.objects.update_or_create(
            reference="BATCH-LI-4475",
            defaults=dict(
                mine_site=site, mineral="Lithium concentrate", tonnes=900, grade=22.9,
                stage=BatchStage.PROCESSING, location="Kitwe Hydromet Plant", transaction=tx_processing,
            ),
        )
        invoice_partial, _ = Invoice.objects.update_or_create(
            reference="INV-4475",
            defaults=dict(
                seller_organisation=mining_org, buyer_organisation=buyer_org,
                transaction_reference=tx_processing.reference,
                description="Lithium concentrate 900t — TX-2026-4475", amount=900 * 5680,
                advance_percent=30, status=InvoiceStatus.PARTIALLY_PAID,
                issued_at=now - timedelta(days=7), due_at=now + timedelta(days=8),
            ),
        )
        Payment.objects.update_or_create(
            reference="PMT-4475-A",
            defaults=dict(
                invoice=invoice_partial, amount=900 * 5680 * 0.3, method="bank_transfer",
                status=PaymentStatus.CLEARED, received_at=now - timedelta(days=6),
            ),
        )
        invoice_partial.refresh_from_db()
        invoice_partial.refresh_status()
        tx_processing.finance_invoice = invoice_partial
        tx_processing.save(update_fields=["finance_invoice"])

        # -- TX export-ready / shipped: past processing, cleared for export,
        # invoice issued but unpaid (drives outstanding payments up).
        tx_export, _ = Transaction.objects.update_or_create(
            reference="TX-2026-4481",
            defaults=dict(
                rfq=rfq, mine_site=site, buyer_organisation=buyer_org, seller_organisation=mining_org,
                mineral="Lithium concentrate", grade_spec="≥ 22% Li2O", committed_tonnes=420,
                aggregated_tonnes=420, unit_price=5620, currency="USD", incoterm="FOB Lagos",
                destination="Antwerp", stage=LifecycleStage.RFQ_RECEIVED,
            ),
        )
        Commitment.objects.get_or_create(transaction=tx_export, mine_site=site)
        walk_stages(tx_export, {
            LifecycleStage.RFQ_RECEIVED: now - timedelta(days=26),
            LifecycleStage.ACCEPTED: now - timedelta(days=25),
            LifecycleStage.AGGREGATION: now - timedelta(days=24),
            LifecycleStage.SAMPLE_COLLECTED: now - timedelta(days=22),
            LifecycleStage.RESULTS_PUBLISHED: now - timedelta(days=20),
            LifecycleStage.BUYER_QUALITY_ACCEPTANCE: now - timedelta(days=19),
            LifecycleStage.BULK_LOGISTICS: now - timedelta(days=18),
            LifecycleStage.MATERIAL_PICKED_UP: now - timedelta(days=17),
            LifecycleStage.IN_TRANSIT: now - timedelta(days=16),
            LifecycleStage.WAREHOUSE_RECEIVED: now - timedelta(days=14),
            LifecycleStage.PROCESSING_STARTED: now - timedelta(days=12),
            LifecycleStage.PROCESSING_COMPLETED: now - timedelta(days=8),
            LifecycleStage.OUTPUT_RECORDED: now - timedelta(days=7),
            LifecycleStage.POST_PROCESSING_QUALITY: now - timedelta(days=6),
            LifecycleStage.EXPORT_COMPLIANCE: now - timedelta(days=4),
            LifecycleStage.EXPORT_READY: now - timedelta(days=3),
            LifecycleStage.SHIPPED: now - timedelta(days=1),
        })
        MaterialBatch.objects.update_or_create(
            reference="BATCH-LI-4481",
            defaults=dict(
                mine_site=site, mineral="Lithium concentrate", tonnes=420, grade=23.1,
                stage=BatchStage.SHIPPED, location="At sea — Lagos to Antwerp", transaction=tx_export,
            ),
        )
        invoice_unpaid, _ = Invoice.objects.update_or_create(
            reference="INV-4481",
            defaults=dict(
                seller_organisation=mining_org, buyer_organisation=buyer_org,
                transaction_reference=tx_export.reference,
                description="Lithium concentrate 420t — TX-2026-4481", amount=420 * 5620,
                advance_percent=0, status=InvoiceStatus.ISSUED,
                issued_at=now - timedelta(days=2), due_at=now + timedelta(days=13),
            ),
        )
        tx_export.finance_invoice = invoice_unpaid
        tx_export.save(update_fields=["finance_invoice"])

        # -- unallocated stockpile batches (no transaction) and active
        # logistics moves not yet delivered.
        MaterialBatch.objects.update_or_create(
            reference="BATCH-LI-STOCK-2",
            defaults=dict(
                mine_site=site, mineral="Lithium concentrate", tonnes=1150, grade=21.6,
                stage=BatchStage.STOCKPILE, location=f"{site.name} — pad 2", transaction=None,
            ),
        )
        MaterialBatch.objects.update_or_create(
            reference="BATCH-CO-STOCK",
            defaults=dict(
                mine_site=site, mineral="Cobalt hydroxide", tonnes=180, grade=29.4,
                stage=BatchStage.STOCKPILE, location=f"{site.name} — covered store", transaction=None,
            ),
        )
        LogisticsMove.objects.update_or_create(
            reference="MV-4462-C",
            defaults=dict(
                transaction=tx_processing, kind=MoveKind.SAMPLE, carrier_name="SwiftLab Couriers",
                vehicle="LC-3312", driver="B. Okoro", from_location=site.name,
                to_location="Kitwe Metallurgical Institute", tonnes="0.015", status=MoveStatus.ASSIGNED,
                assigned_at=now - timedelta(hours=10), picked_up_at=None, arrived_at=None,
            ),
        )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded ecosystem demo: {rfq.reference} -> {tx.reference} "
            f"(stage={tx.stage}), sample={sample.reference}, lot={lot.reference}, "
            f"run={run.reference}, shipment={shipment.reference}, invoice={invoice.reference}. "
            f"Plus: {tx_transit.reference} (in_transit), {tx_processing.reference} (processing_started, "
            f"partially paid), {tx_export.reference} (shipped, unpaid invoice), "
            f"2 open/accepted extra RFQs, 2 unallocated stockpile batches, 1 unassigned-in-transit logistics move."
        ))


STAGE_NOTES = {
    LifecycleStage.SAMPLE_COLLECTED: "Sample SMP-4462-A collected from Ijero Lithium Pit A.",
    LifecycleStage.RESULTS_PUBLISHED: "Assay results published: 31.2% Li2O, 3.8% moisture.",
    LifecycleStage.MATERIAL_PICKED_UP: "500 t picked up by Beira Corridor Freight convoy BC-22.",
    LifecycleStage.WAREHOUSE_RECEIVED: "499 t received at Kitwe Bonded Warehouse, lot LOT-4462.",
    LifecycleStage.PROCESSING_STARTED: "Processing started at Kitwe Hydromet Plant (499 t input).",
    LifecycleStage.EXPORT_READY: "Export ready — shipment SHP-2026-4462 cleared.",
    LifecycleStage.DELIVERED: "Cargo delivered to buyer destination Izmir.",
    LifecycleStage.PAYMENT_SETTLEMENT: "Settlement of USD 2,800,000 received.",
}


def stage_order(stage):
    from ecosystem.models import stage_index

    return stage_index(stage)
