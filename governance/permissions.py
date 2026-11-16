"""Who may do what in the governance workspace.

A caller's powers come from their own GovernanceMember row and nothing else:
the role a browser remembers is a view preference, never a permission. Staff
accounts act as Super Administrators so the platform team can always reach
access management, but staff gain no authoring or approval power from it.

Separation of duties: the person who wrote a policy cannot approve it, and
the person who recorded a regulatory finding cannot verify it.
"""
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from governance.models import Classification, GovernanceMember, GovernanceRole, MemberStatus

R = GovernanceRole

# Each capability lists the roles that hold it.
CAPABILITIES = {
    # Read every governance record (subject to document classification).
    "read": {R.SUPER_ADMIN, R.EXECUTIVE, R.ANALYST, R.RISK_OFFICER, R.REVIEWER, R.VIEWER},
    # Create and edit policies in draft, risks, findings, stakeholders,
    # milestones, impact assessments and documents.
    "author": {R.EXECUTIVE, R.ANALYST, R.RISK_OFFICER},
    # Move a policy into review, request a revision, recommend it; verify a
    # regulatory finding.
    "review": {R.EXECUTIVE, R.REVIEWER},
    # Approve, reject, publish and archive policies.
    "approve": {R.EXECUTIVE},
    # Escalate or close an enterprise risk.
    "escalate": {R.EXECUTIVE, R.RISK_OFFICER},
    # Invitations, access requests, suspensions and role changes.
    "manage_access": {R.SUPER_ADMIN},
    # The governance audit trail.
    "view_audit": {R.SUPER_ADMIN, R.EXECUTIVE},
    # Generate and download reports.
    "report": {R.SUPER_ADMIN, R.EXECUTIVE, R.ANALYST, R.RISK_OFFICER, R.REVIEWER},
}

# The most sensitive classification each role may open.
CLASSIFICATION_CEILING = {
    R.SUPER_ADMIN: {Classification.INTERNAL},
    R.VIEWER: {Classification.INTERNAL},
    R.REVIEWER: {Classification.INTERNAL, Classification.RESTRICTED},
    R.ANALYST: {Classification.INTERNAL, Classification.RESTRICTED, Classification.CONFIDENTIAL},
    R.RISK_OFFICER: {Classification.INTERNAL, Classification.RESTRICTED, Classification.CONFIDENTIAL},
    R.EXECUTIVE: {Classification.INTERNAL, Classification.RESTRICTED, Classification.CONFIDENTIAL},
}

_CACHE_ATTR = "_governance_member"


def member(user):
    """The caller's active membership, or None."""
    if not user.is_authenticated or not user.is_active:
        return None
    if not hasattr(user, _CACHE_ATTR):
        setattr(
            user,
            _CACHE_ATTR,
            GovernanceMember.objects.filter(user=user, status=MemberStatus.ACTIVE).first(),
        )
    return getattr(user, _CACHE_ATTR)


def role(user):
    found = member(user)
    if found:
        return found.role
    if user.is_authenticated and user.is_active and user.is_staff:
        return R.SUPER_ADMIN
    return None


def has(user, capability):
    current = role(user)
    return current is not None and current in CAPABILITIES[capability]


def require(user, capability, message=None):
    if not has(user, capability):
        raise PermissionDenied(message or "Your governance role does not allow this action.")


def capabilities(user):
    return {name: has(user, name) for name in CAPABILITIES}


def classifications(user):
    current = role(user)
    return CLASSIFICATION_CEILING.get(current, set())


class IsGovernanceMember(BasePermission):
    message = "You do not have access to the governance workspace."

    def has_permission(self, request, view):
        return has(request.user, "read")
