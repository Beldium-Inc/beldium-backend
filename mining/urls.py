from django.urls import path
from rest_framework.routers import DefaultRouter

from mining.views import (
    ApplicationViewSet,
    DocumentRecordViewSet,
    EnvRecordViewSet,
    EquipmentViewSet,
    InfoRequestViewSet,
    InspectionViewSet,
    LicenceDocViewSet,
    MineSiteViewSet,
    MiningAuditViewSet,
    MiningCapabilityView,
    MiningChecklistView,
    MiningDashboardView,
    MiningOrganisationProfileViewSet,
    MiningReportView,
    NonConformityViewSet,
    PendingReviewViewSet,
    SafetyIncidentViewSet,
    SampleViewSet,
)

router = DefaultRouter()
router.register("sites", MineSiteViewSet, basename="mining-site")
router.register("organisation-profiles", MiningOrganisationProfileViewSet, basename="mining-organisation-profile")
router.register("non-conformities", NonConformityViewSet, basename="mining-non-conformity")
router.register("inspections", InspectionViewSet, basename="mining-inspection")
router.register("samples", SampleViewSet, basename="mining-sample")
router.register("environmental-records", EnvRecordViewSet, basename="mining-env-record")
router.register("safety-incidents", SafetyIncidentViewSet, basename="mining-safety-incident")
router.register("equipment", EquipmentViewSet, basename="mining-equipment")
router.register("applications", ApplicationViewSet, basename="mining-application")
router.register("pending-reviews", PendingReviewViewSet, basename="mining-pending-review")
router.register("info-requests", InfoRequestViewSet, basename="mining-info-request")
router.register("licences", LicenceDocViewSet, basename="mining-licence")
router.register("documents", DocumentRecordViewSet, basename="mining-document")
router.register("audit", MiningAuditViewSet, basename="mining-audit")

urlpatterns = [
    path("dashboard/", MiningDashboardView.as_view(), name="mining-dashboard"),
    path("me/", MiningCapabilityView.as_view(), name="mining-capabilities"),
    path("checklist/", MiningChecklistView.as_view(), name="mining-checklist"),
    path("reports/", MiningReportView.as_view(), name="mining-report"),
]
urlpatterns += router.urls
