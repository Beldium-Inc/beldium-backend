from rest_framework import serializers

from ecosystem.models import (
    Commitment,
    LifecycleStage,
    LogisticsMove,
    MaterialBatch,
    Rfq,
    Transaction,
    TransactionStageEvent,
)


class RfqSerializer(serializers.ModelSerializer):
    buyer_name = serializers.CharField(source="buyer_organisation.name", read_only=True)
    seller_name = serializers.CharField(source="seller_organisation.name", read_only=True)

    class Meta:
        model = Rfq
        fields = [
            "id", "reference", "buyer_organisation", "buyer_name", "seller_organisation",
            "seller_name", "mineral", "grade_spec", "quantity_requested", "unit",
            "indicative_price", "currency", "incoterm", "destination", "received_at",
            "respond_by", "notes", "status", "committed_quantity", "decided_at",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]


class TransactionStageEventSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = TransactionStageEvent
        fields = ["id", "stage", "occurred_at", "actor", "actor_email", "note"]
        read_only_fields = ["id"]


class MaterialBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialBatch
        fields = [
            "id", "reference", "mine_site", "mineral", "tonnes", "grade", "stage",
            "location", "transaction", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]


class LogisticsMoveSerializer(serializers.ModelSerializer):
    carrier = serializers.CharField(read_only=True)

    class Meta:
        model = LogisticsMove
        fields = [
            "id", "reference", "transaction", "batch", "kind", "carrier_company",
            "carrier_name", "carrier", "vehicle", "driver", "from_location",
            "to_location", "tonnes", "status", "assigned_at", "picked_up_at",
            "arrived_at", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]


class CommitmentSerializer(serializers.ModelSerializer):
    requested = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    committed = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    aggregated = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    fulfilment_percent = serializers.IntegerField(read_only=True)

    class Meta:
        model = Commitment
        fields = [
            "id", "transaction", "mine_site", "requested", "committed",
            "aggregated", "remaining", "fulfilment_percent", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class TransactionSerializer(serializers.ModelSerializer):
    buyer_name = serializers.CharField(source="buyer_organisation.name", read_only=True)
    seller_name = serializers.CharField(source="seller_organisation.name", read_only=True)
    mine_site_name = serializers.CharField(source="mine_site.name", read_only=True)
    progress_percent = serializers.IntegerField(read_only=True)
    value = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    is_closed = serializers.BooleanField(read_only=True)
    stage_events = TransactionStageEventSerializer(many=True, read_only=True)
    batches = MaterialBatchSerializer(many=True, read_only=True)
    logistics_moves = LogisticsMoveSerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id", "reference", "rfq", "mine_site", "mine_site_name",
            "buyer_organisation", "buyer_name", "seller_organisation", "seller_name",
            "mineral", "grade_spec", "committed_tonnes", "aggregated_tonnes",
            "unit_price", "currency", "incoterm", "destination", "stage",
            "quality_sample", "warehousing_lot", "processing_run",
            "export_shipment", "finance_invoice", "progress_percent", "value",
            "is_closed", "stage_events", "batches", "logistics_moves",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]


class TransactionListSerializer(TransactionSerializer):
    """Lighter shape for list views — drops the nested collections."""

    class Meta(TransactionSerializer.Meta):
        fields = [f for f in TransactionSerializer.Meta.fields if f not in {"stage_events", "batches", "logistics_moves"}]


class StageAdvanceSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=LifecycleStage.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")
