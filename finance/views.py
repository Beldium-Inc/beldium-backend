from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from finance.models import Invoice, InvoiceStatus, Payment
from finance.permissions import IsFinanceParticipant, party_organisation_ids
from finance.serializers import InvoiceSerializer, PaymentSerializer
from organisations.access import is_operator as _is_operator, is_regulator as _is_regulator


class InvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = InvoiceSerializer
    permission_classes = [IsAuthenticated, IsFinanceParticipant]

    def get_queryset(self):
        user = self.request.user
        qs = Invoice.objects.select_related("seller_organisation", "buyer_organisation").prefetch_related("payments")
        if _is_operator(user) or _is_regulator(user):
            return qs
        org_ids = party_organisation_ids(user)
        return qs.filter(models_q(org_ids))

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, status=InvoiceStatus.ISSUED, issued_at=timezone.now())


class PaymentViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSerializer
    permission_classes = [IsAuthenticated, IsFinanceParticipant]

    def get_queryset(self):
        user = self.request.user
        qs = Payment.objects.select_related("invoice")
        if _is_operator(user) or _is_regulator(user):
            return qs
        org_ids = party_organisation_ids(user)
        return qs.filter(models_q(org_ids, prefix="invoice__"))

    def perform_create(self, serializer):
        serializer.save(recorded_by=self.request.user, status="cleared", received_at=timezone.now())


def models_q(org_ids, prefix=""):
    from django.db.models import Q

    return Q(**{f"{prefix}seller_organisation_id__in": org_ids}) | Q(**{f"{prefix}buyer_organisation_id__in": org_ids})
