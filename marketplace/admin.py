from django.contrib import admin

from marketplace import models

for model in [
    models.SellerProfile,
    models.MarketplaceAccessGrant,
    models.MarketplaceProduct,
    models.ProductDocument,
    models.ProductComplianceCheck,
    models.MarketplaceOrder,
    models.MarketplaceLicense,
    models.MarketplaceDispute,
    models.MarketplaceNotification,
    models.MarketplaceReport,
    models.PaymentWebhookEvent,
]:
    admin.site.register(model)


@admin.register(models.MarketplaceAuditEvent)
class MarketplaceAuditEventAdmin(admin.ModelAdmin):
    """Audit rows are looked at, never added, changed or deleted here."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
