from django.urls import path
from rest_framework.routers import DefaultRouter

from vault import views

router = DefaultRouter()
router.register("documents", views.VaultDocumentViewSet, basename="vault-document")

urlpatterns = [
    path("sectors/", views.VaultSectorsView.as_view(), name="vault-sectors"),
] + router.urls
