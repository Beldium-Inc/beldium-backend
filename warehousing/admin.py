from django.contrib import admin

from warehousing import models

for model in [models.WarehouseOperator, models.WarehousingAccessGrant, models.Facility, models.StorageZone, models.InventoryLot, models.Inspection]:
    admin.site.register(model)


class ReviewRecordAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in [
    models.WarehousingApplication,
    models.DomainReview,
    models.WarehousingCondition,
    models.WarehousingDocument,
    models.InformationRequest,
    models.Notification,
    models.WarehousingReport,
]:
    admin.site.register(model, ReviewRecordAdmin)
