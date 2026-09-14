from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from warehousing.models import WarehousingAccessGrant, WarehouseOperator
from organisations.models import OrganisationMembership

EDIT_ROLES = {"owner", "admin", "compliance_manager"}


def warehouse_ids(user):
    if not user.is_authenticated or not user.is_active:
        return WarehouseOperator.objects.none().values_list("id", flat=True)
    if user.is_staff:
        return WarehouseOperator.objects.values_list("id", flat=True)
    return WarehouseOperator.objects.filter(
        Q(organisation__memberships__user=user, organisation__memberships__is_active=True)
        | Q(access_grants__user=user, access_grants__is_active=True)
    ).distinct().values_list("id", flat=True)


def can_review(user, warehouse):
    return user.is_active and (
        user.is_staff
        or WarehousingAccessGrant.objects.filter(user=user, warehouse=warehouse, role="reviewer", is_active=True).exists()
    )


def can_edit(user, warehouse):
    return user.is_active and (
        user.is_staff
        or OrganisationMembership.objects.filter(
            organisation=warehouse.organisation, user=user, is_active=True, role__in=EDIT_ROLES
        ).exists()
    )


def assert_editor(user, warehouse):
    if not can_edit(user, warehouse):
        raise PermissionDenied("Only active warehouse administrators may edit this record.")


def assert_reviewer(user, application):
    if not can_review(user, application.warehouse):
        raise PermissionDenied("You do not have warehousing review authority for this warehouse.")
    if not user.is_staff and application.reviewer_id != user.pk:
        raise PermissionDenied("Only the assigned reviewer may review this application.")
