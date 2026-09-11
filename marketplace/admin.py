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
    models.MarketplaceAuditEvent,
    models.MarketplaceReport,
    models.PaymentWebhookEvent,
]:
    admin.site.register(model)
