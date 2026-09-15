from rest_framework import serializers

from finance.models import Invoice, Payment


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = [
            "id", "invoice", "reference", "amount", "currency", "method",
            "external_reference", "status", "received_at", "recorded_by",
            "notes", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "recorded_by", "created_at", "updated_at"]


class InvoiceSerializer(serializers.ModelSerializer):
    amount_paid = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    amount_outstanding = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    seller_organisation_name = serializers.CharField(source="seller_organisation.name", read_only=True)
    buyer_organisation_name = serializers.CharField(source="buyer_organisation.name", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id", "reference", "seller_organisation", "seller_organisation_name",
            "buyer_organisation", "buyer_organisation_name", "transaction_reference",
            "description", "amount", "currency", "advance_percent", "status",
            "issued_at", "due_at", "paid_at", "notes", "amount_paid",
            "amount_outstanding", "payments", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "reference", "status", "paid_at", "created_at", "updated_at"]
