from django.urls import path
from rest_framework.routers import DefaultRouter

from mining.views import (
    ApplicationViewSet,
    DocumentRecordViewSet,
    EnvRecordViewSet,
    EquipmentViewSet,
    InfoRequestViewSet,
    InspectionViewSet,
    InventoryItemViewSet,
    LicenceDocViewSet,
    MineSiteViewSet,
    MiningAuditViewSet,
    MiningCapabilityView,
    MiningChecklistView,
    MiningDashboardView,
    MiningOrganisationProfileViewSet,
    MiningOrganisationVerificationView,
    MiningReportView,
    NonConformityViewSet,
    PendingReviewViewSet,
    ProductionRecordViewSet,
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
router.register("production", ProductionRecordViewSet, basename="mining-production")
router.register("inventory", InventoryItemViewSet, basename="mining-inventory")
router.register("applications", ApplicationViewSet, basename="mining-application")
router.register("pending-reviews", PendingReviewViewSet, basename="mining-pending-review")
router.register("info-requests", InfoRequestViewSet, basename="mining-info-request")
router.register("licences", LicenceDocViewSet, basename="mining-licence")
router.register("documents", DocumentRecordViewSet, basename="mining-document")
router.register("audit", MiningAuditViewSet, basename="mining-audit")

urlpatterns = [
    path("dashboard/", MiningDashboardView.as_view(), name="mining-dashboard"),
    path("organisation-verification/", MiningOrganisationVerificationView.as_view(), name="mining-organisation-verification"),
    path(
        "organisation-verification/<uuid:pk>/<str:decision>/",
        MiningOrganisationVerificationView.as_view(),
        name="mining-organisation-decision",
    ),
    path("me/", MiningCapabilityView.as_view(), name="mining-capabilities"),
    path("checklist/", MiningChecklistView.as_view(), name="mining-checklist"),
    path("reports/", MiningReportView.as_view(), name="mining-report"),
]
urlpatterns += router.urls
