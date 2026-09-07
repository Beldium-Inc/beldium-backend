from rest_framework.routers import DefaultRouter

from compliance.views import ComplianceApplicationViewSet, DashboardViewSet

router = DefaultRouter()
router.register("compliance-applications", ComplianceApplicationViewSet, basename="compliance-application")
router.register("dashboard", DashboardViewSet, basename="dashboard")
urlpatterns = router.urls
