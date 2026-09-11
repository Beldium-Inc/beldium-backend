from django.contrib import admin
from logistics import models

for model in [models.LogisticsCompany, models.LogisticsAccessGrant, models.OperatingLocation,
              models.Vehicle, models.Driver]:
    admin.site.register(model)


class ReviewRecordAdmin(admin.ModelAdmin):
    """Review transitions go through the permission-checked API, not raw forms."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [models.LogisticsApplication, models.DomainReview, models.ApprovalCondition,
              models.LogisticsDocument, models.InformationRequest, models.ScopeRestriction,
              models.MonitoringEvent, models.LogisticsReport]:
    admin.site.register(model, ReviewRecordAdmin)
