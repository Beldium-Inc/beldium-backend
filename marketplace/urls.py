from django.urls import path
from rest_framework.routers import DefaultRouter

from marketplace import views

router = DefaultRouter()
for prefix, view, basename in [
    ("sellers", views.SellerViewSet, "marketplace-seller"),
    ("access-grants", views.AccessGrantViewSet, "marketplace-access-grant"),
    ("products", views.ProductViewSet, "marketplace-product"),
    ("documents", views.DocumentViewSet, "marketplace-document"),
    ("orders", views.OrderViewSet, "marketplace-order"),
    ("licenses", views.LicenseViewSet, "marketplace-license"),
    ("disputes", views.DisputeViewSet, "marketplace-dispute"),
    ("notifications", views.NotificationViewSet, "marketplace-notification"),
    ("audit", views.AuditViewSet, "marketplace-audit"),
    ("reports", views.ReportViewSet, "marketplace-report"),
    ("payments", views.PaymentWebhookViewSet, "marketplace-payment"),
]:
    router.register(prefix, view, basename=basename)

urlpatterns = router.urls + [
    path(name + "/", views.SummaryViewSet.as_view({"get": name}), name="marketplace-" + name)
    for name in ["me", "dashboard", "risk"]
]

