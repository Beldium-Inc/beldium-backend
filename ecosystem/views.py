from django.db.models import Q, Sum
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ecosystem.models import (
    BatchStage,
    Commitment,
    LifecycleStage,
    LogisticsMove,
    MaterialBatch,
    Rfq,
    RfqStatus,
    Transaction,
    TransactionStageEvent,
    stage_index,
)
from ecosystem.permissions import IsEcosystemParticipant, visible_organisation_filter
from ecosystem.serializers import (
    CommitmentSerializer,
    LogisticsMoveSerializer,
    MaterialBatchSerializer,
    RfqSerializer,
    StageAdvanceSerializer,
    TransactionListSerializer,
    TransactionSerializer,
)
from finance.models import Invoice, InvoiceStatus


def _org_filter(user, buyer_field="buyer_organisation_id", seller_field="seller_organisation_id"):
    org_ids = visible_organisation_filter(user)
    if org_ids is None:
        return Q()
    return Q(**{f"{buyer_field}__in": org_ids}) | Q(**{f"{seller_field}__in": org_ids})


def _transaction_queryset(user):
    qs = Transaction.objects.select_related(
        "rfq", "mine_site", "buyer_organisation", "seller_organisation"
    ).prefetch_related("stage_events", "batches", "logistics_moves")
    org_ids = visible_organisation_filter(user)
    if org_ids is None:
        return qs
    return qs.filter(_org_filter(user) | Q(mine_site__organisation_id__in=org_ids))


class RfqViewSet(viewsets.ModelViewSet):
    serializer_class = RfqSerializer
    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def get_queryset(self):
        qs = Rfq.objects.select_related("buyer_organisation", "seller_organisation")
        return qs.filter(_org_filter(self.request.user))


class TransactionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def get_serializer_class(self):
        return TransactionListSerializer if self.action == "list" else TransactionSerializer

    def get_queryset(self):
        return _transaction_queryset(self.request.user)


class TransactionAdvanceStageView(APIView):
    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def post(self, request, pk):
        transaction = _transaction_queryset(request.user).get(pk=pk)
        serializer = StageAdvanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            transaction.advance_stage(
                serializer.validated_data["stage"],
                actor=request.user,
                note=serializer.validated_data.get("note", ""),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(TransactionSerializer(transaction).data, status=200)


class MaterialBatchViewSet(viewsets.ModelViewSet):
    serializer_class = MaterialBatchSerializer
    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def get_queryset(self):
        qs = MaterialBatch.objects.select_related("mine_site", "transaction")
        org_ids = visible_organisation_filter(self.request.user)
        if org_ids is None:
            return qs
        return qs.filter(mine_site__organisation_id__in=org_ids)


class LogisticsMoveViewSet(viewsets.ModelViewSet):
    serializer_class = LogisticsMoveSerializer
    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def get_queryset(self):
        qs = LogisticsMove.objects.select_related("transaction", "batch", "carrier_company")
        org_ids = visible_organisation_filter(self.request.user)
        if org_ids is None:
            return qs
        return qs.filter(
            Q(transaction__buyer_organisation_id__in=org_ids)
            | Q(transaction__seller_organisation_id__in=org_ids)
        )


class CommitmentViewSet(viewsets.ModelViewSet):
    serializer_class = CommitmentSerializer
    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def get_queryset(self):
        qs = Commitment.objects.select_related("transaction", "mine_site", "transaction__rfq")
        org_ids = visible_organisation_filter(self.request.user)
        if org_ids is None:
            return qs
        return qs.filter(mine_site__organisation_id__in=org_ids)


class EcosystemDashboardView(APIView):
    """Aggregate dashboard shape the Miner Hub prototype's dashboard needs,
    computed live from real querysets rather than hardcoded."""

    permission_classes = [IsAuthenticated, IsEcosystemParticipant]

    def get(self, request):
        user = request.user
        org_ids = visible_organisation_filter(user)

        rfq_qs = Rfq.objects.all()
        tx_qs = _transaction_queryset(user)
        batch_qs = MaterialBatch.objects.select_related("mine_site")
        invoice_qs = Invoice.objects.all()
        event_qs = TransactionStageEvent.objects.select_related("transaction")

        if org_ids is not None:
            rfq_qs = rfq_qs.filter(_org_filter(user))
            batch_qs = batch_qs.filter(mine_site__organisation_id__in=org_ids)
            invoice_qs = invoice_qs.filter(
                Q(seller_organisation_id__in=org_ids) | Q(buyer_organisation_id__in=org_ids)
            )
            event_qs = event_qs.filter(
                Q(transaction__buyer_organisation_id__in=org_ids)
                | Q(transaction__seller_organisation_id__in=org_ids)
                | Q(transaction__mine_site__organisation_id__in=org_ids)
            )

        active_tx = [tx for tx in tx_qs if not tx.is_closed]

        available_inventory = batch_qs.filter(stage=BatchStage.STOCKPILE).aggregate(t=Sum("tonnes"))["t"] or 0
        in_transit = batch_qs.filter(stage=BatchStage.IN_TRANSIT).aggregate(t=Sum("tonnes"))["t"] or 0
        in_processing = batch_qs.filter(stage=BatchStage.PROCESSING).aggregate(t=Sum("tonnes"))["t"] or 0
        export_ready = batch_qs.filter(stage=BatchStage.EXPORT_READY).aggregate(t=Sum("tonnes"))["t"] or 0

        outstanding_invoices = invoice_qs.exclude(status=InvoiceStatus.PAID).exclude(status=InvoiceStatus.CANCELLED)
        outstanding_payments = sum((inv.amount_outstanding for inv in outstanding_invoices), start=0)

        actions_required = []
        for r in rfq_qs.filter(status=RfqStatus.OPEN):
            actions_required.append({
                "id": f"act-rfq-{r.id}",
                "title": f"Respond to {r.reference}",
                "detail": f"{r.buyer_organisation.name} requests {r.quantity_requested} {r.unit} of {r.mineral}.",
                "domain": "marketplace",
                "severity": "high",
                "due_at": r.respond_by,
                "transaction_id": None,
            })
        for tx in active_tx:
            if tx.aggregated_tonnes < tx.committed_tonnes:
                pct = round((tx.aggregated_tonnes / tx.committed_tonnes) * 100) if tx.committed_tonnes else 0
                actions_required.append({
                    "id": f"act-agg-{tx.id}",
                    "title": f"Aggregate {tx.committed_tonnes - tx.aggregated_tonnes} for {tx.reference}",
                    "detail": f"{tx.buyer_organisation.name} commitment is {pct}% aggregated.",
                    "domain": "marketplace",
                    "severity": "medium",
                    "due_at": None,
                    "transaction_id": str(tx.id),
                })
            if tx.stage == LifecycleStage.IN_TRANSIT:
                actions_required.append({
                    "id": f"act-tr-{tx.id}",
                    "title": f"Confirm warehouse receipt for {tx.reference}",
                    "detail": "Bulk consignment en route.",
                    "domain": "logistics",
                    "severity": "medium",
                    "due_at": None,
                    "transaction_id": str(tx.id),
                })
        for inv in outstanding_invoices.filter(status=InvoiceStatus.OVERDUE):
            actions_required.append({
                "id": f"act-pay-{inv.id}",
                "title": f"Chase settlement for {inv.reference}",
                "detail": f"{inv.amount_outstanding} {inv.currency} overdue since {inv.due_at}.",
                "domain": "finance",
                "severity": "high",
                "due_at": inv.due_at,
                "transaction_id": None,
            })

        activity_feed = list(
            event_qs.order_by("-occurred_at")[:20].values(
                "id", "stage", "occurred_at", "note", "transaction__reference", "transaction__mineral"
            )
        )

        # Compliance status folds in the mine site's own compliance_score
        # (already tracked by the existing mining app), not restated here.
        from mining.models import MineSite

        site_qs = MineSite.objects.all()
        if org_ids is not None:
            site_qs = site_qs.filter(organisation_id__in=org_ids)
        site_scores = list(site_qs.values_list("compliance_score", flat=True))
        compliance_status = round(sum(site_scores) / len(site_scores)) if site_scores else None

        data = {
            "compliance_status": compliance_status,
            "active_rfqs": rfq_qs.filter(status=RfqStatus.OPEN).count(),
            "active_supply_commitments": len(active_tx),
            "aggregated_to_date": tx_qs.aggregate(t=Sum("aggregated_tonnes"))["t"] or 0,
            "available_inventory": available_inventory,
            "in_transit": in_transit,
            "in_processing": in_processing,
            "export_ready": export_ready,
            "active_transactions": len(active_tx),
            "outstanding_payments": outstanding_payments,
            "unread_notifications": 0,
            "actions_required": actions_required,
            "action_centre_items": actions_required,
            "live_ecosystem_activity": [
                {
                    "id": str(e["id"]),
                    "at": e["occurred_at"],
                    "stage": e["stage"],
                    "message": e["note"] or f"{e['transaction__mineral']} moved to {e['stage']}",
                    "reference": e["transaction__reference"],
                }
                for e in activity_feed
            ],
        }
        return Response(data)
