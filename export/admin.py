from django.contrib import admin

from export import models

for model in [models.Exporter, models.ExportAccessGrant, models.Product, models.Buyer, models.Shipment]:
    admin.site.register(model)


class ReviewRecordAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [
    models.ExportApplication,
    models.DomainReview,
    models.ExportCondition,
    models.ExportDocument,
    models.InformationRequest,
    models.Notification,
    models.ExportReport,
]:
    admin.site.register(model, ReviewRecordAdmin)
