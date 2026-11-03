"""Remove test accounts that the admin site cannot delete on its own.

A test account that went through the logistics application has records that
point back to it with PROTECT (the application it created, the documents it
uploaded). The admin site also refuses to delete logistics review records on
purpose, so deleting such a user from the Users page fails.

This clears those logistics records first, then the organisations only that
account belonged to, then the account. Each account is handled in its own
savepoint: if anything outside logistics still protects it (export,
marketplace or warehousing records, for example), nothing for that account is
deleted and the blocking records are reported instead.

Only for test accounts. Staff and superuser accounts are always refused.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import ProtectedError, Q

from logistics.models import (
    DocumentNote,
    InformationRequest,
    LogisticsApplication,
    LogisticsCompany,
    LogisticsDocument,
    LogisticsReport,
    RequestResponse,
    ScopeRestriction,
)
from organisations.models import Organisation, OrganisationInvitation


@dataclass
class AccountPlan:
    """What removing one account will take with it. Shown before confirming."""

    user: object
    applications: list = field(default_factory=list)
    documents: int = 0
    organisations: list = field(default_factory=list)
    refused_reason: str = ""


@dataclass
class AccountResult:
    email: str
    deleted: bool
    message: str


def _logistics_applications(user):
    return LogisticsApplication.objects.filter(created_by=user)


def _sole_organisations(user, selected_ids):
    """Organisations whose every member is one of the accounts being removed."""
    candidates = Organisation.objects.filter(memberships__user=user).distinct()
    return [
        organisation for organisation in candidates
        if not organisation.memberships.exclude(user_id__in=selected_ids).exists()
    ]


def refusal_reason(user, acting_user=None):
    if acting_user is not None and user.pk == acting_user.pk:
        return "You cannot remove your own account."
    if user.is_staff or user.is_superuser:
        return "Staff and superuser accounts are never removed this way."
    return ""


def plan_removal(users, acting_user=None):
    selected_ids = {u.pk for u in users}
    plans = []
    for user in users:
        plan = AccountPlan(user=user, refused_reason=refusal_reason(user, acting_user))
        if not plan.refused_reason:
            applications = _logistics_applications(user)
            plan.applications = [str(a.company) for a in applications.select_related("company")]
            plan.documents = LogisticsDocument.objects.filter(
                Q(application__in=applications) | Q(uploaded_by=user)
            ).distinct().count()
            plan.organisations = [o.name for o in _sole_organisations(user, selected_ids)]
        plans.append(plan)
    return plans


def _describe_blockers(error):
    objects = list(error.protected_objects)[:5]
    return ", ".join(f"{obj._meta.verbose_name} ({obj.pk})" for obj in objects)


def _remove_one(user, selected_ids):
    applications = _logistics_applications(user)
    # Read the company ids now: once the applications are gone this lookup is empty.
    company_ids = list(LogisticsCompany.objects.filter(application__in=applications).values_list("pk", flat=True))
    companies = LogisticsCompany.objects.filter(pk__in=company_ids)
    own_documents = LogisticsDocument.objects.filter(Q(application__in=applications) | Q(uploaded_by=user))

    # Restrictions point at documents and conditions with PROTECT, so they go first.
    ScopeRestriction.objects.filter(
        Q(company__in=companies)
        | Q(source_document__in=own_documents)
        | Q(source_condition__application__in=applications)
    ).delete()
    # Anything this account wrote in logistics, including inside other applications.
    DocumentNote.objects.filter(author=user).delete()
    RequestResponse.objects.filter(author=user).delete()
    InformationRequest.objects.filter(raised_by=user).delete()
    LogisticsReport.objects.filter(requested_by=user).delete()
    own_documents.delete()
    # Cascades to sections, conditions, the remaining documents and requests.
    applications.delete()
    companies.delete()

    OrganisationInvitation.objects.filter(invited_by=user).delete()
    kept = []
    for organisation in _sole_organisations(user, selected_ids):
        try:
            with transaction.atomic():
                organisation.delete()
        except ProtectedError as error:
            kept.append(f"{organisation.name} (still used by {_describe_blockers(error)})")

    user.delete()
    return kept


def remove_test_accounts(users, acting_user=None):
    """Remove each account in its own savepoint. Returns one result per account."""
    selected_ids = {u.pk for u in users}
    results = []
    for user in users:
        email = user.email
        reason = refusal_reason(user, acting_user)
        if reason:
            results.append(AccountResult(email, False, reason))
            continue
        try:
            with transaction.atomic():
                kept = _remove_one(user, selected_ids)
        except ProtectedError as error:
            results.append(AccountResult(
                email, False,
                f"Not removed. Records outside logistics still point to it: {_describe_blockers(error)}",
            ))
            continue
        message = "Removed with its logistics records."
        if kept:
            message += " Kept organisations: " + "; ".join(kept)
        results.append(AccountResult(email, True, message))
    return results
