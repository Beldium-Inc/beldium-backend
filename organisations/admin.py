from django.contrib import admin

from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership, SectorAccessGrant

admin.site.register(Organisation)
admin.site.register(OrganisationMembership)
admin.site.register(OrganisationInvitation)
admin.site.register(JoinRequest)


@admin.register(SectorAccessGrant)
class SectorAccessGrantAdmin(admin.ModelAdmin):
    list_display = ["organisation", "sector", "is_active", "granted_by", "created_at", "revoked_at"]
    list_filter = ["sector", "is_active"]
