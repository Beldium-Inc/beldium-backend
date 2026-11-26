"""Structured transaction evidence for authorised compliance and admin users."""
from organisations.access import is_operator, is_regulator
from organisations.models import OrganisationMembership


EXPORT_ROLES = {
    "owner", "admin", "compliance_manager", "mining_compliance_officer",
    "reviewer", "inspector",
}


def can_export(user, transaction):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_staff or user.is_superuser:
        return True
    if is_regulator(user):
        return False
    if is_operator(user) and OrganisationMembership.objects.filter(
        user=user, is_active=True, role__in=EXPORT_ROLES,
        organisation__organisation_type__in=("compliance_partner", "inspection_body"),
        organisation__verification_status="verified",
    ).exists():
        return True
    organisation_ids = {
        transaction.buyer_organisation_id,
        transaction.seller_organisation_id,
        transaction.mine_site.organisation_id,
    }
    return OrganisationMembership.objects.filter(
        user=user,
        organisation_id__in=organisation_ids,
        is_active=True,
        role__in=EXPORT_ROLES,
    ).exists()


def build_evidence(transaction):
    """Return references only; document bytes remain behind their own access checks."""
    documents = []
    if transaction.warehousing_lot_id:
        documents.extend({"sector": "warehousing", "id": str(doc.id), "type": doc.document_type,
                          "title": doc.title, "reference": doc.reference, "version": doc.version,
                          "status": doc.status}
                         for doc in transaction.warehousing_lot.documents.all())
    if transaction.export_shipment_id:
        documents.extend({"sector": "export", "id": str(doc.id), "type": doc.document_type,
                          "title": doc.title, "reference": doc.reference, "version": doc.version,
                          "status": doc.status}
                         for doc in transaction.export_shipment.documents.all())
    certificates = []
    if transaction.quality_sample_id:
        certificates = [
            {"id": str(cert.id), "reference": cert.reference, "status": cert.status,
             "valid_until": cert.valid_until.isoformat() if cert.valid_until else None}
            for cert in transaction.quality_sample.certificates.all()
        ]
    return {
        "transaction": {
            "id": str(transaction.id),
            "reference": transaction.reference,
            "rfq_reference": transaction.rfq.reference,
            "buyer_organisation_id": str(transaction.buyer_organisation_id),
            "seller_organisation_id": str(transaction.seller_organisation_id),
            "mine_site_id": str(transaction.mine_site_id),
            "mineral": transaction.mineral,
            "committed_tonnes": str(transaction.committed_tonnes),
            "aggregated_tonnes": str(transaction.aggregated_tonnes),
            "unit_price": str(transaction.unit_price),
            "currency": transaction.currency,
            "stage": transaction.stage,
            "created_at": transaction.created_at.isoformat(),
        },
        "stage_events": [
            {"id": str(event.id), "stage": event.stage,
             "occurred_at": event.occurred_at.isoformat(), "note": event.note}
            for event in transaction.stage_events.all().order_by("occurred_at", "id")
        ],
        "linked_records": {
            "quality_sample_id": str(transaction.quality_sample_id) if transaction.quality_sample_id else None,
            "warehousing_lot_id": str(transaction.warehousing_lot_id) if transaction.warehousing_lot_id else None,
            "processing_run_id": str(transaction.processing_run_id) if transaction.processing_run_id else None,
            "export_shipment_id": str(transaction.export_shipment_id) if transaction.export_shipment_id else None,
            "finance_invoice_id": str(transaction.finance_invoice_id) if transaction.finance_invoice_id else None,
            "batch_ids": [str(batch.id) for batch in transaction.batches.all()],
            "logistics_move_ids": [str(move.id) for move in transaction.logistics_moves.all()],
        },
        "document_references": documents,
        "certificate_references": certificates,
    }
