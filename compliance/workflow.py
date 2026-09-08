"""Application state rules shared by submission and staff review."""
from django.utils import timezone

from compliance.models import ApplicationStatus as S
from common.exceptions import ConflictError


REVIEW_STATES = {S.UNDER_REVIEW, S.ACTION_REQUIRED, S.CONDITIONALLY_APPROVED}
TRANSITIONS = {
    S.DRAFT: {S.UNDER_REVIEW},
    S.UNDER_REVIEW: {S.ACTION_REQUIRED, S.CONDITIONALLY_APPROVED, S.VERIFIED, S.REJECTED},
    S.ACTION_REQUIRED: {S.UNDER_REVIEW, S.REJECTED},
    S.CONDITIONALLY_APPROVED: {S.ACTION_REQUIRED, S.VERIFIED, S.REJECTED},
    S.REJECTED: {S.UNDER_REVIEW},
    S.VERIFIED: set(),
}


def require_review(application):
    if application.status not in REVIEW_STATES:
        raise ConflictError("The application is not open for review.", code="application_not_under_review")


def transition(application, target, *, reviewer=None):
    if target not in TRANSITIONS[application.status]:
        raise ConflictError(f"Cannot move from {application.status} to {target}.", code="invalid_application_transition")
    application.status = target
    application.save()
    organisation = application.organisation
    organisation.verification_status = {
        S.VERIFIED: "verified", S.REJECTED: "rejected",
    }.get(target, "under_review")
    organisation.verified_at = timezone.now() if target == S.VERIFIED else None
    organisation.verified_by = reviewer
    organisation.rejection_reason = application.review_notes if target == S.REJECTED else ""
    organisation.submitted_at = application.submitted_at
    organisation.save(update_fields=["verification_status", "verified_at", "verified_by", "rejection_reason", "submitted_at", "updated_at"])
