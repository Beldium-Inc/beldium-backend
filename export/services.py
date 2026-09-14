from django.db.models import Q
from django.utils import timezone

from accounts.audit import record_account_event
from accounts.models import User
from common.exceptions import ConflictError
from export import models as m

EDITABLE = {"draft", "awaiting_information", "rejected"}
REVIEWABLE = {"under_review", "awaiting_information", "conditionally_approved"}


def audit(request, exporter, event, **metadata):
    record_account_event(
        request,
        "export." + event,
        exporter_id=str(exporter.pk),
        organisation_id=str(exporter.organisation_id),
        **metadata,
    )


def notify(exporter, title, body):
    recipients = User.objects.filter(is_active=True).filter(
        Q(is_staff=True)
        | Q(organisation_memberships__organisation=exporter.organisation, organisation_memberships__is_active=True)
        | Q(exportaccessgrant__exporter=exporter, exportaccessgrant__is_active=True)
    ).distinct()
    m.Notification.objects.bulk_create(
        [m.Notification(exporter=exporter, recipient=user, title=title, body=body) for user in recipients]
    )


def assert_state(application, states):
    if application.status not in states:
        raise ConflictError(
            f"This action is unavailable while the application is {application.status}.",
            code="invalid_application_state",
        )


def initialise(application):
    m.DomainReview.objects.bulk_create(
        [m.DomainReview(application=application, key=key) for key in m.Domain.values]
    )
    application.domain_weights = {key: 1 for key in m.Domain.values}
    application.save(update_fields=["domain_weights"])


def valid_documents(application):
    return application.documents.filter(is_current=True).exclude(status="rejected").filter(
        Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate())
    )


def progress(application):
    sections = list(application.sections.all())
    required = [section for section in sections if section.applicable]
    supplied = set(valid_documents(application).values_list("domain", flat=True))
    missing_data = [section.key for section in required if not section.data]
    missing_evidence = [section.key for section in required if section.key not in supplied]
    checks = {
        "account": bool(application.created_by.is_active and application.created_by.email_verified_at),
        "exporter": bool(application.exporter.contact_email and application.exporter.product_categories),
        "products": application.exporter.products.filter(is_active=True).exists(),
        "buyers": application.exporter.buyers.filter(is_active=True).exists(),
        "shipments": application.exporter.shipments.exclude(status__in=["cancelled"]).exists(),
        "sections": bool(required) and not missing_data,
        "documents": bool(required) and not missing_evidence,
        "requests": not application.requests.exclude(status="accepted").exists(),
    }
    return {
        "percent": round(sum(checks.values()) / len(checks) * 100),
        "checks": checks,
        "missing_sections": missing_data,
        "missing_document_domains": missing_evidence,
        "documents_submitted": application.documents.filter(is_current=True).count(),
        "domains_required": len(required),
    }


def risk(application):
    sections = [section for section in application.sections.all() if section.applicable]
    total = sum(application.domain_weights.get(section.key, 1) for section in sections)
    score = round(
        sum(section.score * application.domain_weights.get(section.key, 1) for section in sections) / total,
        2,
    ) if total else 0
    return {
        "compliance_score": score,
        "risk_band": "low" if score >= 85 else "medium" if score >= 65 else "high",
        "policy_version": application.policy_version,
        "weights": application.domain_weights,
        "domains": {section.key: {"score": section.score, "status": section.status} for section in sections},
    }


def require_complete(application):
    result = progress(application)
    if result["percent"] != 100:
        raise ConflictError("Complete the export application and resolve outstanding requests.", code="application_incomplete", details=result)
    invalid = application.documents.filter(is_current=True).filter(Q(status="rejected") | Q(expires_on__lt=timezone.localdate()))
    if invalid.exists():
        raise ConflictError("Replace rejected or expired evidence before submission.", code="invalid_evidence")


def require_approval(application, conditional=False):
    require_complete(application)
    if application.documents.filter(is_current=True).exclude(status="verified").exists():
        raise ConflictError("All current evidence must be verified.", code="documents_not_verified")
    allowed = {"passed", "attention"} if conditional else {"passed"}
    if application.sections.filter(applicable=True).exclude(status__in=allowed).exists():
        raise ConflictError("Complete the required domain sign-offs.", code="domains_not_cleared")
    if not conditional and application.conditions.filter(cleared_at__isnull=True).exists():
        raise ConflictError("Clear all approval conditions first.", code="conditions_not_cleared")
