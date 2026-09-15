from django.urls import path
from rest_framework.routers import DefaultRouter

from warehousing import views

router = DefaultRouter()
for prefix, view, basename in [
    ("warehouses", views.WarehouseOperatorViewSet, "warehousing-warehouse"),
    ("facilities", views.FacilityViewSet, "warehousing-facility"),
    ("zones", views.StorageZoneViewSet, "warehousing-zone"),
    ("lots", views.InventoryLotViewSet, "warehousing-lot"),
    ("inspections", views.InspectionViewSet, "warehousing-inspection"),
    ("incidents", views.IncidentViewSet, "warehousing-incident"),
    ("release-requests", views.ReleaseRequestViewSet, "warehousing-release-request"),
    ("monitoring-alerts", views.MonitoringAlertViewSet, "warehousing-monitoring-alert"),
    ("inspectors", views.InspectorViewSet, "warehousing-inspector"),
    ("certificates", views.CertificateViewSet, "warehousing-certificate"),
    ("access-grants", views.GrantViewSet, "warehousing-access-grant"),
    ("applications", views.ApplicationViewSet, "warehousing-application"),
    ("documents", views.DocumentViewSet, "warehousing-document"),
    ("requests", views.RequestViewSet, "warehousing-request"),
    ("conditions", views.ConditionViewSet, "warehousing-condition"),
    ("notifications", views.NotificationViewSet, "warehousing-notification"),
    ("reports", views.ReportViewSet, "warehousing-report"),
]:
    router.register(prefix, view, basename=basename)

urlpatterns = router.urls + [
    path(name + "/", views.SummaryViewSet.as_view({"get": name}), name="warehousing-" + name)
    for name in ["me", "dashboard", "risk", "audit"]
]
