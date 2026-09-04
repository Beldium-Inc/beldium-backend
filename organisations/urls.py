from django.urls import path
from rest_framework.routers import DefaultRouter

from organisations.views import InvitationAcceptanceView, JoinRequestViewSet, OrganisationViewSet

router = DefaultRouter()
router.register("organisations", OrganisationViewSet, basename="organisation")
router.register("join-requests", JoinRequestViewSet, basename="join-request")

urlpatterns = [path("invitations/accept/", InvitationAcceptanceView.as_view(), name="accept-invitation")]
urlpatterns += router.urls
