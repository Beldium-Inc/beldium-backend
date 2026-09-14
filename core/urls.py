from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/", include("organisations.urls")),
    path("api/v1/", include("compliance.urls")),
    path("api/v1/processing/", include("processing.urls")),
    path("api/v1/logistics/", include("logistics.urls")),
    path("api/v1/export/", include("export.urls")),
    path("api/v1/mining/", include("mining.urls")),
    path("api/v1/marketplace/", include("marketplace.urls")),
    path("api/v1/quality/", include("quality.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]

# MEDIA_ROOT is deliberately not routed here. Compliance uploads are private, and
# django.views.static.serve applies no access control at all, so every stored
# document would be readable by URL. They are served instead by the authenticated
# download actions on ComplianceApplicationViewSet.
