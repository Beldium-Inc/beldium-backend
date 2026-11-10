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
from django.core.exceptions import ObjectDoesNotExist

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
                .select_related("organisation__compliance_application")
                .prefetch_related("organisation__sector_grants")
                .only(
                    "role",
                    "organisation__id",
                    "organisation__organisation_type",
                    "organisation__verification_status",
                    "organisation__compliance_application__sector",
                )
            ),
        )
    return getattr(user, _CACHE_ATTR)


def organisation_ids(user):
    """Every organisation the caller is an active member of."""
    if not user.is_authenticated:
        return []
    return [m.organisation_id for m in memberships(user)]


def registered_sector(organisation):
    """The sector a partner organisation applied to work in, or "".

    Blank for an organisation with no onboarding application and for one that
    applied before the sector was recorded; the vetting desk files those.
    """
    try:
        return organisation.compliance_application.sector
    except ObjectDoesNotExist:
        return ""


def granted_sectors(organisation):
    """The sectors a regulator organisation was approved for, or None.

    None means the organisation holds no grant rows at all: it predates
    regulator access requests and keeps the rule it had (its registered
    sector, or every sector when none is recorded). An organisation with
    grant rows sees only the sectors whose grant is still active, so a
    revoked grant leaves it seeing nothing rather than everything.
    """
    grants = list(organisation.sector_grants.all())
    if not grants:
        return None
    return {grant.sector for grant in grants if grant.is_active}


def sector_allowed(organisation, sector):
    """Is a vetted privileged organisation allowed into ``sector``?

    ``sector`` None asks whether it is allowed into any sector at all.
    """
    if organisation.organisation_type == OrganisationType.REGULATOR:
        granted = granted_sectors(organisation)
        if granted is not None:
            return bool(granted) if sector is None else sector in granted
    return sector is None or registered_sector(organisation) in {"", sector}


def regulator_granted(user, sector):
    """Does the caller belong to a verified regulator organisation with an
    active grant for ``sector``? Used by the sectors whose register is
    otherwise opened only by per-company grants (export, warehousing,
    marketplace), so an approved regulator reads them without a grant row
    per company, and an older regulator organisation gains nothing new."""
    if not user.is_authenticated:
        return False
    for membership in memberships(user):
        organisation = membership.organisation
        if organisation.organisation_type != OrganisationType.REGULATOR:
            continue
        if organisation.verification_status != "verified":
            continue
        granted = granted_sectors(organisation)
        if granted and sector in granted:
            return True
    return False


def _counts_in(membership, sector):
    """Does this membership give its holder standing in ``sector``?

    Desk and oversight power is granted for the sector the organisation was
    vetted in, not for every dashboard: a Quality & Control partner is not a
    logistics reviewer. An organisation with no sector on record predates the
    rule and keeps the access it had.
    """
    organisation = membership.organisation
    if organisation.organisation_type not in PRIVILEGED_TYPES:
        return True
    if organisation.verification_status != "verified":
        return False
    return sector_allowed(organisation, sector)


def audience(user, sector=None):
    """``operator``, ``regulator``, ``member``, or None.

    Pass the vertical's ``sector`` to ask about that dashboard specifically;
    without it the answer is whether the caller holds the audience anywhere.

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
    types = {m.organisation.organisation_type for m in memberships(user) if _counts_in(m, sector)}
    if OrganisationType.COMPLIANCE_PARTNER in types or OrganisationType.INSPECTION_BODY in types:
        return OPERATOR
    if OrganisationType.REGULATOR in types:
        return REGULATOR
    if types:
        return MEMBER
    return None


def is_operator(user, sector=None):
    return audience(user, sector) == OPERATOR


def is_regulator(user, sector=None):
    return audience(user, sector) == REGULATOR


def can_decide(user, decision_roles, sector=None):
    """May take a review action, given this vertical's set of desk roles."""
    if not is_operator(user, sector):
        return False
    if user.is_staff or user.is_superuser:
        return True
    return any(m.role in decision_roles for m in memberships(user) if _counts_in(m, sector))
