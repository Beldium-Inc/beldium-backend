"""Who may see and change what in the Mining Compliance vertical.

Same three-audience shape as ``processing.permissions`` — operator (desk),
regulator (read-only oversight), member (the mining company itself) — built
on the shared resolver in ``organisations.access`` so a security fix made
there (e.g. the unverified-organisation-type gate) applies here too, without
a second copy to forget.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from organisations.access import (
    MEMBER,
    OPERATOR,
    REGULATOR,
    audience as _audience,
    can_decide as _can_decide,
    is_operator,
    is_regulator,
    memberships,
    organisation_ids,
)
from organisations.models import MembershipRole

MINER = "miner"

# Desk roles that may act on a review, as opposed to merely reading it.
DECISION_ROLES = {
    MembershipRole.OWNER,
    MembershipRole.ADMIN,
    MembershipRole.REVIEWER,
    MembershipRole.INSPECTOR,
    MembershipRole.COMPLIANCE_MANAGER,
    MembershipRole.MINING_COMPLIANCE_OFFICER,
}

# Applicant-side roles that may edit their own submission.
APPLICANT_ROLES = {
    MembershipRole.OWNER,
    MembershipRole.ADMIN,
    MembershipRole.COMPLIANCE_MANAGER,
    MembershipRole.MINING_COMPLIANCE_OFFICER,
}


def audience(user):
    """``operator``, ``regulator``, ``miner``, or None."""
    role = _audience(user)
    return MINER if role == MEMBER else role


def can_decide(user):
    return _can_decide(user, DECISION_ROLES)


def owns_site(user, site):
    """May this caller write records against that mine site?

    The desk and the regulator address the whole register; anyone else is
    confined to the sites their own organisation operates.
    """
    if is_operator(user):
        return True
    if site is None or site.organisation_id is None:
        return False
    return site.organisation_id in set(organisation_ids(user))


def can_edit_application(user, application):
    if user.is_staff or user.is_superuser:
        return True
    if application.created_by_id == user.id:
        return True
    owning = {application.organisation_id, getattr(application.site, "organisation_id", None)}
    owning.discard(None)
    if not owning:
        return False
    return any(m.organisation_id in owning and m.role in APPLICANT_ROLES for m in memberships(user))


def capabilities(user):
    role = audience(user)
    return {
        "audience": role,
        "can_decide": can_decide(user),
        "can_read_register": role in {OPERATOR, REGULATOR},
        "is_staff": bool(user.is_authenticated and user.is_staff),
    }


class IsMiningParticipant(BasePermission):
    message = "You do not have access to the mining compliance register."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and audience(request.user))


class IsMiningOperator(BasePermission):
    message = "Only the compliance operator desk can change this record."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return bool(audience(user))
        return can_decide(user)
