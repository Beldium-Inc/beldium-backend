from rest_framework.permissions import BasePermission, SAFE_METHODS

from organisations.models import MembershipRole, OrganisationMembership


def has_organisation_role(user, organisation_id, roles):
    if user.is_staff or user.is_superuser:
        return True
    return OrganisationMembership.objects.filter(
        user=user,
        organisation_id=organisation_id,
        is_active=True,
        role__in=roles,
    ).exists()


class IsOrganisationMember(BasePermission):
    def has_object_permission(self, request, view, obj):
        organisation = obj if obj.__class__.__name__ == "Organisation" else obj.organisation
        return has_organisation_role(request.user, organisation.id, MembershipRole.values)


class IsOrganisationAdministrator(BasePermission):
    admin_roles = [MembershipRole.OWNER, MembershipRole.ADMIN]

    def has_object_permission(self, request, view, obj):
        organisation = obj if obj.__class__.__name__ == "Organisation" else obj.organisation
        if request.method in SAFE_METHODS:
            return has_organisation_role(request.user, organisation.id, MembershipRole.values)
        return has_organisation_role(request.user, organisation.id, self.admin_roles)
