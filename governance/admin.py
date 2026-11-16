from django.contrib import admin

from governance import models

for model in [
    models.GovernanceMember,
    models.Policy,
    models.Risk,
    models.RegulatoryFinding,
    models.Stakeholder,
    models.Milestone,
    models.ImpactAssessment,
    models.ImpactScenario,
    models.GovernanceDocument,
]:
    admin.site.register(model)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Records that must not be changed outside the workflow."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(models.PolicyEvent, ReadOnlyAdmin)
admin.site.register(models.GovernanceInvitation, ReadOnlyAdmin)
