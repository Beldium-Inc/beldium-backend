from django.urls import path
from rest_framework.routers import DefaultRouter

from processing.views import (
    ComplianceReportViewSet,
    EnvironmentalAlertViewSet,
    IncidentViewSet,
    InspectionViewSet,
    NonConformityViewSet,
    ProcessingApplicationViewSet,
    ProcessingAuditViewSet,
    ProcessingCapabilityView,
    ProcessingDashboardView,
    ProcessingDocumentViewSet,
    ProcessorViewSet,
    TraceabilityRunViewSet,
)

router = DefaultRouter()
router.register("processors", ProcessorViewSet, basename="processor")
router.register("applications", ProcessingApplicationViewSet, basename="processing-application")
router.register("non-conformities", NonConformityViewSet, basename="processing-non-conformity")
router.register("inspections", InspectionViewSet, basename="processing-inspection")
router.register("environmental-alerts", EnvironmentalAlertViewSet, basename="processing-environmental-alert")
router.register("incidents", IncidentViewSet, basename="processing-incident")
router.register("runs", TraceabilityRunViewSet, basename="processing-run")
router.register("documents", ProcessingDocumentViewSet, basename="processing-document")
router.register("reports", ComplianceReportViewSet, basename="processing-report")
router.register("audit", ProcessingAuditViewSet, basename="processing-audit")

urlpatterns = [
    path("dashboard/", ProcessingDashboardView.as_view(), name="processing-dashboard"),
    path("me/", ProcessingCapabilityView.as_view(), name="processing-capabilities"),
]
urlpatterns += router.urls
