from django.urls import path
from rest_framework.routers import DefaultRouter

from export import views

router = DefaultRouter()
for prefix, view, basename in [
    ("exporters", views.ExporterViewSet, "export-exporter"),
    ("products", views.ProductViewSet, "export-product"),
    ("buyers", views.BuyerViewSet, "export-buyer"),
    ("shipments", views.ShipmentViewSet, "export-shipment"),
    ("access-grants", views.GrantViewSet, "export-access-grant"),
    ("applications", views.ApplicationViewSet, "export-application"),
    ("documents", views.DocumentViewSet, "export-document"),
    ("requests", views.RequestViewSet, "export-request"),
    ("conditions", views.ConditionViewSet, "export-condition"),
    ("notifications", views.NotificationViewSet, "export-notification"),
    ("reports", views.ReportViewSet, "export-report"),
]:
    router.register(prefix, view, basename=basename)

urlpatterns = router.urls + [
    path(name + "/", views.SummaryViewSet.as_view({"get": name}), name="export-" + name)
    for name in ["me", "dashboard", "risk", "audit"]
]
