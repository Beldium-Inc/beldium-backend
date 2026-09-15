"""Settlement and invoicing domain.

``marketplace.MarketplaceOrder`` already carries a lightweight
``payment_status`` for its own buy-now flow; this app is the fuller
settlement ledger for the cross-domain transaction spine in ``ecosystem`` —
an ``Invoice`` raised against a ``Transaction`` and the ``Payment`` rows
received against it. It keeps the same status vocabulary shape
(pending/paid/...) so a dashboard summing "outstanding" across both concepts
reads consistently.

``Invoice`` has no hard FK to ``ecosystem.Transaction`` — it only carries the
transaction's reference string — so this app has no import-time dependency on
``ecosystem``. The FK lives the other way round instead
(``ecosystem.Transaction.finance_invoice``), which is the direction the
dashboard actually queries.
"""
import secrets

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from common.models import TimeStampedModel


def invoice_reference():
    return f"INV-{timezone.now().year}-{secrets.token_hex(4).upper()}"


def payment_reference():
    return f"PMT-{timezone.now().year}-{secrets.token_hex(4).upper()}"


class InvoiceStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    PARTIALLY_PAID = "partially_paid", "Partially paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"
    DISPUTED = "disputed", "Disputed"
    CANCELLED = "cancelled", "Cancelled"


class PaymentMethod(models.TextChoices):
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    LETTER_OF_CREDIT = "letter_of_credit", "Letter of credit"
    ESCROW = "escrow", "Escrow"
    CARD = "card", "Card"
    CASH = "cash", "Cash"
    OTHER = "other", "Other"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CLEARED = "cleared", "Cleared"
    FAILED = "failed", "Failed"
    REVERSED = "reversed", "Reversed"


class Invoice(TimeStampedModel):
    reference = models.CharField(max_length=50, unique=True, default=invoice_reference, editable=False)
    seller_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, related_name="invoices_issued"
    )
    buyer_organisation = models.ForeignKey(
        "organisations.Organisation", on_delete=models.PROTECT, related_name="invoices_received"
    )
    transaction_reference = models.CharField(max_length=50, blank=True, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    advance_percent = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT, db_index=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    @property
    def amount_paid(self):
        total = self.payments.filter(status=PaymentStatus.CLEARED).aggregate(models.Sum("amount"))["amount__sum"]
        return total or 0

    @property
    def amount_outstanding(self):
        return max(self.amount - self.amount_paid, 0)

    def refresh_status(self):
        """Recompute status from recorded payments. Called after each payment write."""
        if self.status == InvoiceStatus.CANCELLED:
            return
        paid = self.amount_paid
        if paid >= self.amount and self.amount > 0:
            self.status = InvoiceStatus.PAID
            self.paid_at = self.paid_at or timezone.now()
        elif paid > 0:
            self.status = InvoiceStatus.PARTIALLY_PAID
        elif self.due_at and self.due_at < timezone.now() and self.status in {InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID}:
            self.status = InvoiceStatus.OVERDUE
        self.save(update_fields=["status", "paid_at", "updated_at"])


class Payment(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    reference = models.CharField(max_length=50, unique=True, default=payment_reference, editable=False)
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="USD")
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.BANK_TRANSFER)
    external_reference = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)
    received_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.invoice.refresh_status()
