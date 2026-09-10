from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from organisations.models import OrganisationMembership
from logistics.models import LogisticsAccessGrant, LogisticsCompany

EDIT_ROLES = {'owner', 'admin', 'compliance_manager'}


def company_ids(user):
    if not user.is_authenticated or not user.is_active:
        return LogisticsCompany.objects.none().values_list('id', flat=True)
    if user.is_staff:
        return LogisticsCompany.objects.values_list('id', flat=True)
    return LogisticsCompany.objects.filter(
        Q(organisation__memberships__user=user, organisation__memberships__is_active=True) |
        Q(access_grants__user=user, access_grants__is_active=True)
    ).distinct().values_list('id', flat=True)


def can_review(user, company):
    return user.is_active and (user.is_staff or LogisticsAccessGrant.objects.filter(
        user=user, company=company, role='reviewer', is_active=True).exists())


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
