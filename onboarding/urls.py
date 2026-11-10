from django.urls import path
from rest_framework.routers import DefaultRouter

from onboarding.views import (
    OnboardingMeView,
    OnboardingOptionsView,
    ParticipantApplicationViewSet,
    RegulatorAccessRequestViewSet,
)

router = DefaultRouter()
router.register("participants", ParticipantApplicationViewSet, basename="participant-application")
router.register("regulator-requests", RegulatorAccessRequestViewSet, basename="regulator-request")

urlpatterns = [
    path("options/", OnboardingOptionsView.as_view(), name="onboarding-options"),
    path("me/", OnboardingMeView.as_view(), name="onboarding-me"),
]
urlpatterns += router.urls
