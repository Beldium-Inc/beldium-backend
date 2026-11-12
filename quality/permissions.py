"""The one permission module for Q&C.

Every check on a Q&C record asks this module what the caller may do. Rights
come from the access grid (quality/roles.py, stored in QualityRoleGrant) and
the caller's approved, in date role assignments.

Transition rule. Users who have never held a stored assignment still get the
role worked out the old way (staff, organisation type), mapped to the nearest
code of the 12. Once a user has held any approved assignment, only stored
assignments count, so ending an assignment removes access at once.
TODO(Q&C Partner, Emmanuel): give every live user a stored assignment, then
remove the fallback in _legacy_codes.
"""
from django.db.models import Q
from django.utils import timezone

from quality import roles as r


def _today():
    return timezone.localdate()


def active_assignments(user):
    from quality.models import QualityRoleAssignment, RoleAssignmentStatus

    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return QualityRoleAssignment.objects.none()
    today = _today()
    return (
        QualityRoleAssignment.objects.filter(
            user=user, status=RoleAssignmentStatus.ACTIVE, valid_from__lte=today,
        )
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        # A suspended or unverified organisation takes its people's roles
        # with it at once.
        .filter(Q(organisation__isnull=True) | Q(organisation__verification_status="verified"))
        .select_related("role", "organisation")
    )


def uses_stored_roles(user):
    from quality.models import QualityRoleAssignment, RoleAssignmentStatus

    if not user or not getattr(user, "is_authenticated", False):
        return False
    return QualityRoleAssignment.objects.filter(
        user=user, status__in=[RoleAssignmentStatus.ACTIVE, RoleAssignmentStatus.ENDED],
    ).exists()


def _legacy_codes(user):
    """Codes for a user with no stored assignment, from the old derivation."""
    from organisations.models import OrganisationType
    from quality import services

    role = services.legacy_derive_role(user)
    if role == "operator":
        return {r.QC}
    if role == "miner":
        return {r.SUP}
    if role == "buyer":
        return {r.BYR}
    if role == "partner":
        org = services.user_organisation(user)
        org_type = getattr(org, "organisation_type", None)
        if org_type == OrganisationType.LABORATORY:
            return {r.LAB}
        if org_type == OrganisationType.INSPECTION_BODY:
            return {r.INS}
    # A compliance partner or a regulator has no code among the 12.
    # TODO(Q&C Partner): say which of the 12 these organisations map to.
    return set()


def role_codes(user):
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return set()
    if uses_stored_roles(user):
        return {assignment.role_id for assignment in active_assignments(user)}
    return _legacy_codes(user)


def permissions_for(user):
    """{record_type: [actions]} and the set of record types seen in a restricted view."""
    from quality.models import QualityRoleGrant

    codes = role_codes(user)
    rights = {}
    restricted = set()
    full_read = set()
    for grant in QualityRoleGrant.objects.filter(role_id__in=codes):
        actions = set(rights.get(grant.record_type, []))
        actions.update(r.letters_to_actions(grant.letters))
        rights[grant.record_type] = [action for action in r.ACTIONS if action in actions]
        if grant.restricted:
            restricted.add(grant.record_type)
        else:
            full_read.add(grant.record_type)
    # A view is restricted only when no role the user holds gives a full view.
    return rights, sorted(restricted - full_read)


def can(user, record_type, action):
    rights, _ = permissions_for(user)
    return action in rights.get(record_type, [])


def require(user, record_type, action, *, message=None, code="permission_denied"):
    from common.exceptions import AppError

    if not can(user, record_type, action):
        raise AppError(
            message or "You do not have permission to perform this action.",
            code=code, status_code=403,
        )


def has_role(user, *codes):
    return bool(role_codes(user) & set(codes))
