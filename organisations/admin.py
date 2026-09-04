from django.contrib import admin

from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership

admin.site.register(Organisation)
admin.site.register(OrganisationMembership)
admin.site.register(OrganisationInvitation)
admin.site.register(JoinRequest)
