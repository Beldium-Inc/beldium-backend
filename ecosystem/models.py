"""The cross-domain transaction spine.

Every other vertical (marketplace, quality, logistics, warehousing,
processing, export) models one stage of a miner's life cycle in isolation.
This app models the thread that actually runs through all of them for a
single physical trade: a buyer's ``Rfq`` becomes a ``Transaction``, which
aggregates ``MaterialBatch`` stock from a ``mining.MineSite``, moves it with
``LogisticsMove`` records, and links out to the real per-domain record once
that stage is reached (``quality.Sample``, ``warehousing.InventoryLot``,
``processing.TraceabilityRun``, ``export.Shipment``, ``finance.Invoice``).

``Transaction.stage`` and ``TransactionStageEvent`` are the timeline the
miner dashboard renders as "Sample SMP-4510-A collected..." — mirroring the
prototype's ``stageLog`` but as real rows instead of a JSON blob, so the
dashboard's activity feed is a genuine query over stage events, not
re-parsed frontend state.
"""
import secrets

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def rfq_reference():
    return f"RFQ-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def transaction_reference():
    return f"TX-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def batch_reference():
    return f"BATCH-{timezone.now().year}-{secrets.token_hex(3).upper()}"


def move_reference():
    return f"MV-{timezone.now().year}-{secrets.token_hex(3).upper()}"


class LifecycleStage(models.TextChoices):
    """Exact order and ids from the Miner Hub prototype's LIFECYCLE_STAGES."""

    RFQ_RECEIVED = "rfq_received", "RFQ received"
    ACCEPTED = "accepted", "Accepted"
    AGGREGATION = "aggregation", "Aggregation"
    SAMPLE_REQUESTED = "sample_requested", "Sample requested"
    SAMPLE_LOGISTICS = "sample_logistics", "Sample logistics assigned"
    SAMPLE_COLLECTED = "sample_collected", "Sample collected"
    LAB_RECEIVED = "lab_received", "Laboratory received"
    TESTING = "testing", "Testing"
    RESULTS_PUBLISHED = "results_published", "Results published"
    BUYER_QUALITY_ACCEPTANCE = "buyer_quality_acceptance", "Buyer quality acceptance"
    BULK_LOGISTICS = "bulk_logistics", "Bulk logistics assigned"
    MATERIAL_PICKED_UP = "material_picked_up", "Material picked up"
    IN_TRANSIT = "in_transit", "In transit"
    WAREHOUSE_RECEIVED = "warehouse_received", "Warehouse / processor received"
    PROCESSING_STARTED = "processing_started", "Processing started"
    PROCESSING_COMPLETED = "processing_completed", "Processing completed"
    OUTPUT_RECORDED = "output_recorded", "Output tonnage recorded"
    POST_PROCESSING_QUALITY = "post_processing_quality", "Post processing quality"
    EXPORT_COMPLIANCE = "export_compliance", "Export compliance"
    EXPORT_READY = "export_ready", "Export ready"
    SHIPPED = "shipped", "Shipped"
    BUYER_DESTINATION = "buyer_destination", "Buyer destination"
    DELIVERED = "delivered", "Delivered"
    PAYMENT_SETTLEMENT = "payment_settlement", "Payment / settlement"


STAGE_ORDER = [choice.value for choice in LifecycleStage]


def stage_index(stage):
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return -1


class RfqStatus(models.TextChoices):
    OPEN = "open", "Open"
    ACCEPTED = "accepted", "Accepted"
    PARTIALLY_ACCEPTED = "partially_accepted", "Partially accepted"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"


class Rfq(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=rfq_reference, editable=False)
    buyer_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, related_name="rfqs_sent"
    )
    seller_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, related_name="rfqs_received"
    )
    mineral = models.CharField(max_length=120)
    grade_spec = models.CharField(max_length=255, blank=True)
    quantity_requested = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    unit = models.CharField(max_length=20, default="t")
    indicative_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    incoterm = models.CharField(max_length=50, blank=True)
    destination = models.CharField(max_length=150, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    respond_by = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=25, choices=RfqStatus.choices, default=RfqStatus.OPEN, db_index=True)
    committed_quantity = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return self.reference


class Transaction(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=transaction_reference, editable=False)
    rfq = models.ForeignKey(Rfq, on_delete=models.PROTECT, related_name="transactions")
    mine_site = models.ForeignKey("mining.MineSite", on_delete=models.PROTECT, related_name="ecosystem_transactions")
    buyer_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, related_name="transactions_as_buyer"
    )
    seller_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, related_name="transactions_as_seller"
    )
    mineral = models.CharField(max_length=120)
    grade_spec = models.CharField(max_length=255, blank=True)
    committed_tonnes = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    aggregated_tonnes = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    incoterm = models.CharField(max_length=50, blank=True)
    destination = models.CharField(max_length=150, blank=True)
    stage = models.CharField(max_length=30, choices=LifecycleStage.choices, default=LifecycleStage.RFQ_RECEIVED, db_index=True)

    # Cross-links into the real per-domain records once each stage is reached.
    quality_sample = models.ForeignKey(
        "quality.Sample", on_delete=models.SET_NULL, null=True, blank=True, related_name="ecosystem_transactions"
    )
    warehousing_lot = models.ForeignKey(
        "warehousing.InventoryLot", on_delete=models.SET_NULL, null=True, blank=True, related_name="ecosystem_transactions"
    )
    processing_run = models.ForeignKey(
        "processing.TraceabilityRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="ecosystem_transactions"
    )
    export_shipment = models.ForeignKey(
        "export.Shipment", on_delete=models.SET_NULL, null=True, blank=True, related_name="ecosystem_transactions"
    )
    finance_invoice = models.ForeignKey(
        "finance.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="ecosystem_transactions"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    @property
    def is_closed(self):
        return self.stage == LifecycleStage.PAYMENT_SETTLEMENT and bool(
            self.finance_invoice_id and self.finance_invoice.status == "paid"
        )

    @property
    def value(self):
        return self.committed_tonnes * self.unit_price

    @property
    def progress_percent(self):
        idx = stage_index(self.stage)
        return round(((idx + 1) / len(STAGE_ORDER)) * 100) if idx >= 0 else 0

    def advance_stage(self, stage, *, actor=None, note="", occurred_at=None):
        """Move to ``stage`` and log it, refusing to go backwards."""
        if stage_index(stage) < stage_index(self.stage):
            raise ValueError(f"Cannot move transaction backwards from {self.stage} to {stage}")
        self.stage = stage
        self.save(update_fields=["stage", "updated_at"])
        return TransactionStageEvent.objects.create(
            transaction=self,
            stage=stage,
            actor=actor if actor and actor.is_authenticated else None,
            note=note,
            occurred_at=occurred_at or timezone.now(),
        )


class TransactionStageEvent(TimeStampedModel):
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="stage_events")
    stage = models.CharField(max_length=30, choices=LifecycleStage.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["occurred_at"]
        constraints = [
            models.UniqueConstraint(fields=["transaction", "stage"], name="unique_transaction_stage_event")
        ]

    def __str__(self):
        return f"{self.transaction.reference} -> {self.stage}"


class BatchStage(models.TextChoices):
    STOCKPILE = "stockpile", "Stockpile"
    ALLOCATED = "allocated", "Allocated"
    IN_TRANSIT = "in_transit", "In transit"
    WAREHOUSE = "warehouse", "Warehouse"
    PROCESSING = "processing", "Processing"
    EXPORT_READY = "export_ready", "Export ready"
    SHIPPED = "shipped", "Shipped"
    DELIVERED = "delivered", "Delivered"


class MaterialBatch(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=batch_reference, editable=False)
    mine_site = models.ForeignKey("mining.MineSite", on_delete=models.CASCADE, related_name="material_batches")
    mineral = models.CharField(max_length=120)
    tonnes = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    grade = models.DecimalField(max_digits=8, decimal_places=3, default=0)
    stage = models.CharField(max_length=20, choices=BatchStage.choices, default=BatchStage.STOCKPILE, db_index=True)
    location = models.CharField(max_length=255, blank=True)
    transaction = models.ForeignKey(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="batches"
    )

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.reference


class MoveKind(models.TextChoices):
    SAMPLE = "sample", "Sample"
    BULK = "bulk", "Bulk"


class MoveStatus(models.TextChoices):
    ASSIGNED = "assigned", "Assigned"
    IN_TRANSIT = "in_transit", "In transit"
    DELIVERED = "delivered", "Delivered"


class LogisticsMove(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=move_reference, editable=False)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="logistics_moves")
    batch = models.ForeignKey(
        MaterialBatch, on_delete=models.SET_NULL, null=True, blank=True, related_name="logistics_moves"
    )
    kind = models.CharField(max_length=10, choices=MoveKind.choices)
    carrier_company = models.ForeignKey(
        "logistics.LogisticsCompany", on_delete=models.SET_NULL, null=True, blank=True, related_name="ecosystem_moves"
    )
    carrier_name = models.CharField(max_length=200, blank=True, help_text="Free-text carrier when no registered LogisticsCompany applies.")
    vehicle = models.CharField(max_length=120, blank=True)
    driver = models.CharField(max_length=120, blank=True)
    from_location = models.CharField(max_length=255)
    to_location = models.CharField(max_length=255)
    tonnes = models.DecimalField(max_digits=15, decimal_places=3, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=15, choices=MoveStatus.choices, default=MoveStatus.ASSIGNED, db_index=True)
    assigned_at = models.DateTimeField(default=timezone.now)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-assigned_at"]

    def __str__(self):
        return self.reference

    @property
    def carrier(self):
        return self.carrier_company.organisation.name if self.carrier_company_id else self.carrier_name


class Commitment(TimeStampedModel):
    """Supply commitment tracking for a transaction against its originating RFQ.

    Materialised (rather than purely computed) so it can carry its own
    audit trail of when aggregation moved, matching the prototype's
    ``Commitment`` shape (requested/committed/aggregated/remaining/fulfilment).
    """

    transaction = models.OneToOneField(Transaction, on_delete=models.CASCADE, related_name="commitment")
    mine_site = models.ForeignKey("mining.MineSite", on_delete=models.CASCADE, related_name="commitments")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Commitment for {self.transaction.reference}"

    @property
    def requested(self):
        return self.transaction.rfq.quantity_requested

    @property
    def committed(self):
        return self.transaction.committed_tonnes

    @property
    def aggregated(self):
        return self.transaction.aggregated_tonnes

    @property
    def remaining(self):
        return max(self.committed - self.aggregated, 0)

    @property
    def fulfilment_percent(self):
        return round((self.aggregated / self.committed) * 100) if self.committed else 0
