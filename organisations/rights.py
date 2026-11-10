"""What a caller may do, as the section 2 rights table of the backend plan
says, and the shared body of every sector's me/ endpoint (BE-O6).

The table is the default for every sector. Owner and administrator are the
"admin" column, every other membership role is "staff". A person whose join
request is still waiting has no membership and so no rights at all. A member
of a regulator organisation reads the sectors it was granted and writes
nothing.

The server enforces these rights on each endpoint; this list only tells the
frontend which buttons to show.
"""
from django.core.exceptions import ObjectDoesNotExist

from organisations import access
from organisations.models import (
    ADMIN_ROLES,
    JoinRequest,
    OrganisationMembership,
    OrganisationType,
    SECTOR_FOR_TYPE,
)

# One key per row of the section 2 table.
DASHBOARD_VIEW = "dashboard.view"
DASHBOARD_READ_ONLY = "dashboard.read_only"
DRAFTS_EDIT = "drafts.edit"
DOCUMENTS_UPLOAD = "documents.upload"
NONCONFORMITY_EVIDENCE = "nonconformity.submit_evidence"
APPLICATION_SUBMIT = "application.submit"
DECLARATION_SIGN = "declaration.sign"
PROFILE_EDIT = "profile.edit"
TEAM_VIEW = "team.view"
TEAM_VIEW_NAMES = "team.view_names"
TEAM_MANAGE = "team.manage"
JOIN_CODE_MANAGE = "join_code.manage"
FINANCE_VIEW = "finance.view"
REMINDERS_EMAIL = "reminders.email"
REMINDERS_ASSIGNED = "reminders.assigned_only"

ADMIN_RIGHTS = [
    DASHBOARD_VIEW, DRAFTS_EDIT, DOCUMENTS_UPLOAD, NONCONFORMITY_EVIDENCE,
    APPLICATION_SUBMIT, DECLARATION_SIGN, PROFILE_EDIT,
    TEAM_VIEW, TEAM_MANAGE, JOIN_CODE_MANAGE, FINANCE_VIEW, REMINDERS_EMAIL,
]
STAFF_RIGHTS = [
    DASHBOARD_VIEW, DRAFTS_EDIT, DOCUMENTS_UPLOAD, NONCONFORMITY_EVIDENCE,
    TEAM_VIEW_NAMES, REMINDERS_ASSIGNED,
]
# A regulator reads its granted sectors. Its own administrators still run
# their agency's team (officers join with the agency's code), which touches
# no sector record.
REGULATOR_RIGHTS = [DASHBOARD_READ_ONLY]
REGULATOR_ADMIN_RIGHTS = [DASHBOARD_READ_ONLY, TEAM_VIEW, TEAM_MANAGE, JOIN_CODE_MANAGE]
# Beldium operators (Django staff) on the desk.
OPERATOR_RIGHTS = [DASHBOARD_VIEW, TEAM_VIEW]

PARTNER_TYPES = {
    OrganisationType.COMPLIANCE_PARTNER,
    OrganisationType.INSPECTION_BODY,
    OrganisationType.LABORATORY,
}


def is_admin_role(role):
    return role in ADMIN_ROLES


def dashboard_for(organisation):
    """applicant, partner or regulator: which side of the sector this
    organisation sits on."""
    if organisation.organisation_type == OrganisationType.REGULATOR:
        return "regulator"
    if organisation.organisation_type in PARTNER_TYPES:
        return "partner"
    return "applicant"


def rights_for(membership):
    if membership is None or not membership.is_active:
        return []
    admin = is_admin_role(membership.role)
    if membership.organisation.organisation_type == OrganisationType.REGULATOR:
        return list(REGULATOR_ADMIN_RIGHTS if admin else REGULATOR_RIGHTS)
    return list(ADMIN_RIGHTS if admin else STAFF_RIGHTS)


def organisation_matches(organisation, sector):
    """Does this organisation belong on ``sector``'s dashboards?

    True for an exact match, "maybe" for an organisation that records no
    sector (an older partner, or an older regulator with no grants), and
    False otherwise. Exact matches win over maybes when picking one.
    """
    own_sector = SECTOR_FOR_TYPE.get(organisation.organisation_type)
    if own_sector:
        return own_sector == sector
    if organisation.organisation_type == OrganisationType.REGULATOR:
        granted = access.granted_sectors(organisation)
        if granted is not None:
            return sector in granted
        return "maybe"
    try:
        registered = organisation.compliance_application.sector
    except ObjectDoesNotExist:
        registered = ""
    if registered:
        return registered == sector
    return "maybe"


def _pick(rows, sector, get_org):
    exact = [row for row in rows if organisation_matches(get_org(row), sector) is True]
    if exact:
        return exact[0]
    maybe = [row for row in rows if organisation_matches(get_org(row), sector) == "maybe"]
    return maybe[0] if maybe else None


def admin_name(organisation):
    membership = (
        organisation.memberships.filter(is_active=True, role__in=ADMIN_ROLES)
        .select_related("user").order_by("created_at").first()
    )
    if not membership:
        return ""
    user = membership.user
    return user.full_name or user.email


def _organisation_payload(organisation, *, full):
    data = {"id": str(organisation.id), "name": organisation.name}
    if full:
        data.update({
            "beldium_id": organisation.beldium_id,
            "organisation_type": organisation.organisation_type,
            "participant_subtype": organisation.participant_subtype,
            "verification_status": organisation.verification_status,
        })
    return data


def build_me(user, sector):
    """The onboarding truth for ``user`` on ``sector``'s dashboards.

    membership_status is one of:
    ``active``     a member, rights as the table gives
    ``suspended``  suspended by the company admin, no rights
    ``pending``    a join request (or regulator request) is waiting, no rights
    ``none``       nothing yet: send them to the onboarding screen
    A pending or suspended caller gets the organisation's name only, never
    its records.
    """
    payload = {
        "sector": sector,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "full_name": user.full_name,
        },
        "organisation": None,
        "membership_id": None,
        "membership_role": None,
        "membership_status": "none",
        "dashboard": None,
        "rights": [],
        "admin_name": "",
        "granted_sectors": [],
        "pending_join_request": None,
        "regulator_request": None,
    }
    if user.is_staff or user.is_superuser:
        payload.update({"dashboard": "operator", "membership_status": "active", "rights": list(OPERATOR_RIGHTS)})
        return payload

    rows = list(
        OrganisationMembership.objects.filter(user=user)
        .select_related("organisation__compliance_application")
        .prefetch_related("organisation__sector_grants")
        .order_by("-is_active", "created_at")
    )
    active = _pick([m for m in rows if m.is_active], sector, lambda m: m.organisation)
    membership = active or _pick([m for m in rows if not m.is_active], sector, lambda m: m.organisation)

    if membership is not None:
        organisation = membership.organisation
        payload.update({
            "organisation": _organisation_payload(organisation, full=membership.is_active),
            "membership_id": str(membership.id),
            "membership_role": membership.role,
            "membership_status": membership.status,
            "dashboard": dashboard_for(organisation),
            "rights": rights_for(membership),
            "admin_name": admin_name(organisation),
        })
        if membership.is_active and organisation.organisation_type == OrganisationType.REGULATOR:
            payload["granted_sectors"] = sorted(access.granted_sectors(organisation) or [])
        return payload

    pending = list(
        JoinRequest.objects.filter(requester=user, status="pending")
        .select_related("organisation__compliance_application")
        .prefetch_related("organisation__sector_grants")
    )
    join_request = _pick(pending, sector, lambda r: r.organisation)
    if join_request is not None:
        organisation = join_request.organisation
        payload.update({
            "organisation": _organisation_payload(organisation, full=False),
            "membership_role": join_request.requested_role,
            "membership_status": "pending",
            "dashboard": dashboard_for(organisation),
            "admin_name": admin_name(organisation),
            "pending_join_request": {
                "id": str(join_request.id),
                "organisation_name": organisation.name,
                "requested_role": join_request.requested_role,
                "created_at": join_request.created_at.isoformat(),
            },
        })
        return payload

    from onboarding.models import RegulatorAccessRequest

    regulator_request = (
        RegulatorAccessRequest.objects.filter(requester=user).order_by("-created_at").first()
    )
    if regulator_request is not None:
        payload["regulator_request"] = {
            "id": str(regulator_request.id),
            "status": regulator_request.status,
            "agency_name": regulator_request.agency_name,
            "requested_sectors": regulator_request.requested_sectors,
            "approved_sectors": regulator_request.approved_sectors,
            "rejection_reason": regulator_request.rejection_reason,
        }
        if regulator_request.status == "pending":
            payload.update({"membership_status": "pending", "dashboard": "regulator"})
    return payload
