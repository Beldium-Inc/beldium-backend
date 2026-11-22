from django.contrib import admin

from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership, SectorAccessGrant
from organisations.rights import organisation_sector

admin.site.register(Organisation)
admin.site.register(OrganisationInvitation)
admin.site.register(JoinRequest)


@admin.register(OrganisationMembership)
class OrganisationMembershipAdmin(admin.ModelAdmin):
    """Search by a person's email to see which organisation, and which sector, they belong to."""

    list_display = ["user", "organisation", "organisation_type", "sector", "role", "is_active"]
    list_filter = ["organisation__organisation_type", "role", "is_active"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "organisation__name"]
    list_select_related = ["user", "organisation"]

    @admin.display(description="Organisation type")
    def organisation_type(self, obj):
        return obj.organisation.get_organisation_type_display()

    @admin.display(description="Sector")
    def sector(self, obj):
        return organisation_sector(obj.organisation) or "-"


@admin.register(SectorAccessGrant)
class SectorAccessGrantAdmin(admin.ModelAdmin):
    list_display = ["organisation", "sector", "is_active", "granted_by", "created_at", "revoked_at"]
    list_filter = ["sector", "is_active"]
