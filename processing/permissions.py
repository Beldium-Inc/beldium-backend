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

from organisations.models import MembershipRole, OrganisationMembership, OrganisationType

OPERATOR = "operator"
REGULATOR = "regulator"
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

# Types whose membership grants desk or oversight power. That power must wait
# for the platform to have actually verified the organisation is what it
# claims: organisation_type is a field the applicant fills in themselves, so
# an unverified claim of one of these types is worth nothing.
PRIVILEGED_TYPES = {
    OrganisationType.COMPLIANCE_PARTNER,
    OrganisationType.INSPECTION_BODY,
    OrganisationType.REGULATOR,
}


def _memberships(user):
    if not hasattr(user, "_processing_memberships"):
        user._processing_memberships = list(
            OrganisationMembership.objects.filter(user=user, is_active=True)
            .select_related("organisation")
            .only(
                "role",
                "organisation__id",
                "organisation__organisation_type",
                "organisation__verification_status",
            )
        )
    return user._processing_memberships


def organisation_ids(user):
    """Every organisation the caller is an active member of."""
    if not user.is_authenticated:
        return []
    return [m.organisation_id for m in _memberships(user)]


def audience(user):
    """``operator``, ``regulator``, ``processor``, or None.

    Staff are operators. An operator membership outranks a regulator one so a
    person seconded to the desk keeps their review powers.

    A membership in a compliance-partner, inspection-body or regulator
    organisation only counts once that organisation is verified: the type is
    self-declared at signup, so an unverified claim of it must not itself
    unlock desk or oversight access.
    """
    if not user.is_authenticated:
        return None
    if user.is_staff or user.is_superuser:
        return OPERATOR
    types = {
        m.organisation.organisation_type
        for m in _memberships(user)
        if m.organisation.organisation_type not in PRIVILEGED_TYPES
        or m.organisation.verification_status == "verified"
    }
    if OrganisationType.COMPLIANCE_PARTNER in types or OrganisationType.INSPECTION_BODY in types:
        return OPERATOR
    if OrganisationType.REGULATOR in types:
        return REGULATOR
    if types:
        return PROCESSOR
    return None


def is_operator(user):
    return audience(user) == OPERATOR


def is_regulator(user):
    return audience(user) == REGULATOR


def can_decide(user):
    """May take a review action: verify a section, raise a finding, decide."""
    if not is_operator(user):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return any(m.role in DECISION_ROLES for m in _memberships(user))


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
