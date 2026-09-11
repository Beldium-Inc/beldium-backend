from django.urls import path

from mining.views import MiningCapabilityView, MiningDashboardView

urlpatterns = [
    path("dashboard/", MiningDashboardView.as_view(), name="mining-dashboard"),
    path("me/", MiningCapabilityView.as_view(), name="mining-capabilities"),
]
