"""Generic "who is this caller, in compliance terms" resolution.

Every compliance vertical (processing, mining, logistics, ...) sorts a caller
into the same three audiences from the same signal — active organisation
memberships — so this lives once, here, rather than once per vertical where a
copy could quietly drift from a security fix made to another one:

``operator``   the compliance desk — reviews, raises findings, decides
``regulator``  oversight — reads the whole register, changes nothing
``member``     an applicant/registrant — sees only its own records

A caller's audience comes from their organisation memberships, never from a
request parameter: the frontend may store a chosen role in the browser, and
that choice must not be able to grant anything.
"""
from organisations.models import OrganisationMembership, OrganisationType

OPERATOR = "operator"
REGULATOR = "regulator"
MEMBER = "member"

# Types whose membership grants desk or oversight power. That power must wait
# for the platform to have actually verified the organisation is what it
# claims: organisation_type is a field the applicant fills in themselves, so
# an unverified claim of one of these types is worth nothing.
PRIVILEGED_TYPES = {
    OrganisationType.COMPLIANCE_PARTNER,
    OrganisationType.INSPECTION_BODY,
    OrganisationType.REGULATOR,
}

_CACHE_ATTR = "_compliance_memberships"


def memberships(user):
    if not hasattr(user, _CACHE_ATTR):
        setattr(
            user,
            _CACHE_ATTR,
            list(
                OrganisationMembership.objects.filter(user=user, is_active=True)
                .select_related("organisation")
                .only(
                    "role",
                    "organisation__id",
                    "organisation__organisation_type",
                    "organisation__verification_status",
                )
            ),
        )
    return getattr(user, _CACHE_ATTR)


def organisation_ids(user):
    """Every organisation the caller is an active member of."""
    if not user.is_authenticated:
        return []
    return [m.organisation_id for m in memberships(user)]


def audience(user):
    """``operator``, ``regulator``, ``member``, or None.

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
        for m in memberships(user)
        if m.organisation.organisation_type not in PRIVILEGED_TYPES
        or m.organisation.verification_status == "verified"
    }
    if OrganisationType.COMPLIANCE_PARTNER in types or OrganisationType.INSPECTION_BODY in types:
        return OPERATOR
    if OrganisationType.REGULATOR in types:
        return REGULATOR
    if types:
        return MEMBER
    return None


def is_operator(user):
    return audience(user) == OPERATOR


def is_regulator(user):
    return audience(user) == REGULATOR


def can_decide(user, decision_roles):
    """May take a review action, given this vertical's set of desk roles."""
    if not is_operator(user):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return any(m.role in decision_roles for m in memberships(user))
