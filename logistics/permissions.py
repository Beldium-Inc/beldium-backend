from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from organisations import access
from organisations.models import MembershipRole, OrganisationMembership
from logistics.models import LogisticsAccessGrant, LogisticsCompany

EDIT_ROLES = {'owner', 'admin', 'compliance_manager'}

# Compliance-partner roles that may act on a logistics review, as opposed to
# merely reading the register. Mirrors mining.permissions.DECISION_ROLES.
DESK_ROLES = {
    MembershipRole.OWNER,
    MembershipRole.ADMIN,
    MembershipRole.REVIEWER,
    MembershipRole.INSPECTOR,
    MembershipRole.COMPLIANCE_MANAGER,
}


def sees_register(user):
    """The compliance desk and the regulator read every logistics company.

    Same rule as the mining register: the audience comes from a verified
    compliance-partner, inspection-body or regulator membership, so a partner
    that has just been vetted finds the applications waiting for it instead of
    an empty workspace that only a per-company grant could fill.
    """
    return access.audience(user) in {access.OPERATOR, access.REGULATOR}


def is_desk(user):
    return access.can_decide(user, DESK_ROLES)


def company_ids(user):
    if not user.is_authenticated or not user.is_active:
        return LogisticsCompany.objects.none().values_list('id', flat=True)
    if user.is_staff or sees_register(user):
        return LogisticsCompany.objects.values_list('id', flat=True)
    return LogisticsCompany.objects.filter(
        Q(organisation__memberships__user=user, organisation__memberships__is_active=True) |
        Q(access_grants__user=user, access_grants__is_active=True)
    ).distinct().values_list('id', flat=True)


def can_review(user, company):
    if not user.is_active:
        return False
    if user.is_staff:
        return True
    # Nobody reviews a company they belong to, whatever else they hold.
    if company.organisation_id in access.organisation_ids(user):
        return False
    return is_desk(user) or LogisticsAccessGrant.objects.filter(
        user=user, company=company, role='reviewer', is_active=True).exists()


def can_edit(user, company):
    return user.is_active and (user.is_staff or OrganisationMembership.objects.filter(
        organisation=company.organisation, user=user, is_active=True, role__in=EDIT_ROLES).exists())


def assert_editor(user, company):
    if not can_edit(user, company):
        raise PermissionDenied('Only active company administrators may edit this record.')


def assert_reviewer(user, application):
    if not can_review(user, application.company):
        raise PermissionDenied('You do not have logistics review authority for this company.')
    if not user.is_staff and application.reviewer_id != user.pk:
        raise PermissionDenied('Only the assigned reviewer may review this application.')


def can_see_driver_details(user, company):
    return can_review(user, company) or OrganisationMembership.objects.filter(
        organisation=company.organisation, user=user, is_active=True, role__in=EDIT_ROLES).exists()
