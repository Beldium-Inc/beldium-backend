from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from export.models import ExportAccessGrant, Exporter
from organisations.models import OrganisationMembership

EDIT_ROLES = {"owner", "admin", "compliance_manager"}


def exporter_ids(user):
    if not user.is_authenticated or not user.is_active:
        return Exporter.objects.none().values_list("id", flat=True)
    if user.is_staff:
        return Exporter.objects.values_list("id", flat=True)
    return Exporter.objects.filter(
        Q(organisation__memberships__user=user, organisation__memberships__is_active=True)
        | Q(access_grants__user=user, access_grants__is_active=True)
    ).distinct().values_list("id", flat=True)


def can_review(user, exporter):
    return user.is_active and (
        user.is_staff
        or ExportAccessGrant.objects.filter(user=user, exporter=exporter, role="reviewer", is_active=True).exists()
    )


def can_edit(user, exporter):
    return user.is_active and (
        user.is_staff
        or OrganisationMembership.objects.filter(
            organisation=exporter.organisation, user=user, is_active=True, role__in=EDIT_ROLES
        ).exists()
    )


def assert_editor(user, exporter):
    if not can_edit(user, exporter):
        raise PermissionDenied("Only active exporter administrators may edit this record.")


def assert_reviewer(user, application):
    if not can_review(user, application.exporter):
        raise PermissionDenied("You do not have export review authority for this exporter.")
    if not user.is_staff and application.reviewer_id != user.pk:
        raise PermissionDenied("Only the assigned reviewer may review this application.")
