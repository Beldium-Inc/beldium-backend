"""Who may see and change what on the cross-domain transaction spine.

Same three-audience shape as every other vertical: operator (desk, sees
everything), regulator (read-only oversight), party (a miner or buyer sees
only records where their organisation is the seller/buyer/site operator).
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from organisations.access import is_operator, is_regulator, organisation_ids


def party_organisation_ids(user):
    return set(organisation_ids(user))


class IsEcosystemParticipant(BasePermission):
    message = "You do not have access to this record."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


def visible_organisation_filter(user):
    """Returns None if the caller should see everything, else a set of org ids to filter by."""
    if is_operator(user) or is_regulator(user):
        return None
    return party_organisation_ids(user)
