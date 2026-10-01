from django.urls import path

from careers import views

urlpatterns = [
    path("applications/", views.ApplicationCreateView.as_view(), name="careers-application-create"),
    path("partner-applications/", views.PartnerApplicationCreateView.as_view(), name="careers-partner-create"),
    path(
        "partner-applications/<str:application_id>/",
        views.PartnerApplicationStatusView.as_view(),
        name="careers-partner-status",
    ),
    path(
        "partner-applications/<str:application_id>/documents/",
        views.PartnerDocumentUploadView.as_view(),
        name="careers-partner-document",
    ),
    path(
        "partner-applications/<str:application_id>/submit/",
        views.PartnerApplicationSubmitView.as_view(),
        name="careers-partner-submit",
    ),
]
