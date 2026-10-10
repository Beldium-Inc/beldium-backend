from django.urls import path
from rest_framework.routers import DefaultRouter

from quality import views

router = DefaultRouter()
router.register("applications", views.QualityApplicationViewSet, basename="quality-application")
router.register("professional-applications", views.QualityProfessionalApplicationViewSet, basename="quality-professional-application")
router.register("samples", views.SampleViewSet, basename="quality-sample")
router.register("certificates", views.CertificateViewSet, basename="quality-certificate")
router.register("buyer-specs", views.BuyerSpecViewSet, basename="quality-buyer-spec")
router.register("non-conformities", views.NonConformityViewSet, basename="quality-non-conformity")
router.register("notifications", views.QualityNotificationViewSet, basename="quality-notification")

urlpatterns = [
    path("me/", views.SummaryView.as_view(), {"kind": "me"}, name="quality-me"),
    path("dashboard/", views.SummaryView.as_view(), {"kind": "dashboard"}, name="quality-dashboard"),
    path("certificates/verify/<str:verification_hash>/", views.CertificateVerificationView.as_view(), name="quality-certificate-verify"),
] + router.urls
