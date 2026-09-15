"""Who may see and change what in the finance ledger.

Finance is bilateral: an invoice is only meaningful to the two organisations
on it (seller, buyer), plus the shared operator/regulator desk that every
other vertical already recognises via ``organisations.access``. There is no
separate "finance reviewer" role in the prototype and none is implied by the
data model, so operator == any verified compliance-partner/inspection-body
staff already privileged platform-wide, same as the other verticals.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from organisations.access import is_operator, is_regulator, organisation_ids


def audience(user):
    if not user.is_authenticated:
        return None
    if is_operator(user):
        return "operator"
    if is_regulator(user):
        return "regulator"
    return "party"


def party_organisation_ids(user):
    return set(organisation_ids(user))


class IsFinanceParticipant(BasePermission):
    message = "You do not have access to this financial record."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if is_operator(user) or is_regulator(user):
            return request.method in SAFE_METHODS or is_operator(user)
        org_ids = party_organisation_ids(user)
        invoice = obj if hasattr(obj, "seller_organisation_id") else obj.invoice
        is_party = invoice.seller_organisation_id in org_ids or invoice.buyer_organisation_id in org_ids
        if not is_party:
            return False
        if request.method in SAFE_METHODS:
            return True
        # Only the seller (the party being paid) may issue/edit invoices or
        # record payments against them.
        return invoice.seller_organisation_id in org_ids
