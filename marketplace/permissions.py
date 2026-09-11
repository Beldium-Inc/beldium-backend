from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from organisations.models import OrganisationMembership
from marketplace.models import MarketplaceAccessGrant, SellerProfile

EDIT_ROLES = {"owner", "admin", "compliance_manager"}


def seller_ids(user):
    if not user.is_authenticated or not user.is_active:
        return SellerProfile.objects.none().values_list("id", flat=True)
    if user.is_staff:
        return SellerProfile.objects.values_list("id", flat=True)
    return SellerProfile.objects.filter(
        Q(organisation__memberships__user=user, organisation__memberships__is_active=True)
        | Q(access_grants__user=user, access_grants__is_active=True)
    ).distinct().values_list("id", flat=True)


def can_edit(user, seller):
    return user.is_active and (
        user.is_staff
        or OrganisationMembership.objects.filter(
            organisation=seller.organisation,
            user=user,
            is_active=True,
            role__in=EDIT_ROLES,
        ).exists()
    )


def can_review(user, seller):
    return user.is_active and (
        user.is_staff
        or MarketplaceAccessGrant.objects.filter(user=user, seller=seller, role__in={"reviewer", "support"}, is_active=True).exists()
    )


def can_read(user, seller):
    return seller.pk in set(seller_ids(user))


def assert_editor(user, seller):
    if not can_edit(user, seller):
        raise PermissionDenied("Only active seller administrators may edit this marketplace record.")


def assert_reviewer(user, seller):
    if not can_review(user, seller):
        raise PermissionDenied("You do not have marketplace review authority for this seller.")

