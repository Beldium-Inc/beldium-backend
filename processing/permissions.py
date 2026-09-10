"""Who may see and change what in the Processing Compliance vertical.

Three audiences share the same data at different depths:

``operator``   the compliance desk — reviews sections, raises findings, decides
``regulator``  oversight — reads the whole register, changes nothing
``processor``  the applicant/registrant — sees only its own records, and only
               submits its own evidence

A caller's audience comes from their organisation memberships, never from a
request parameter: the frontend stores a chosen role in the browser, and that
choice must not be able to grant anything.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from organisations.access import (
    MEMBER,
    OPERATOR,
    REGULATOR,
    can_decide as _can_decide,
    is_operator,
    is_regulator,
    memberships as _memberships,
    organisation_ids,
)
from organisations.access import audience as _audience
from organisations.models import MembershipRole

# processing's own name for the "applicant" audience — organisations.access
# calls this generic role ``member``, since not every vertical's applicant is
# a processor. audience() below translates one to the other so every existing
# caller (frontend included) keeps seeing "processor" on the wire.
PROCESSOR = "processor"

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
    """``operator``, ``regulator``, ``processor``, or None. See organisations.access.audience."""
    role = _audience(user)
    return PROCESSOR if role == MEMBER else role


def can_decide(user):
    """May take a review action: verify a section, raise a finding, decide."""
    return _can_decide(user, DECISION_ROLES)


def can_edit_application(user, application):
    """May the caller change this application's own submitted content?"""
    if user.is_staff or user.is_superuser:
        return True
    if application.created_by_id == user.id:
        return True
    owning = {application.organisation_id, getattr(application.processor, "organisation_id", None)}
    owning.discard(None)
    if not owning:
        return False
    return any(
        m.organisation_id in owning and m.role in APPLICANT_ROLES for m in _memberships(user)
    )


def owns_processor(user, processor):
    """May this caller write records against that processor?

    The desk and the regulator address the whole register; anyone else is
    confined to the processors their own organisation operates. Without this a
    processor could file a run or an incident against a competitor.
    """
    if is_operator(user):
        return True
    if processor is None:
        return False
    return processor.organisation_id in set(organisation_ids(user))


def capabilities(user):
    """The caller's processing permissions, for the UI to render against."""
    role = audience(user)
    return {
        "audience": role,
        "can_review": can_decide(user),
        "can_decide": can_decide(user),
        "can_read_register": role in {OPERATOR, REGULATOR},
        "is_staff": bool(user.is_authenticated and user.is_staff),
    }


class IsProcessingParticipant(BasePermission):
    """Any authenticated caller with a processing audience may read."""

    message = "You do not have access to the processing compliance register."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and audience(request.user))


class IsProcessingOperator(BasePermission):
    """Read for operators and regulators; write for the operator desk only."""

    message = "Only the compliance operator desk can change this record."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return bool(audience(user))
        return can_decide(user)
