from django.urls import path
from rest_framework.routers import DefaultRouter
from logistics import views

router = DefaultRouter()
for prefix, view, basename in [
    ('companies', views.CompanyViewSet, 'logistics-company'),
    ('locations', views.LocationViewSet, 'logistics-location'),
    ('vehicles', views.VehicleViewSet, 'logistics-vehicle'),
    ('drivers', views.DriverViewSet, 'logistics-driver'),
    ('access-grants', views.GrantViewSet, 'logistics-access-grant'),
    ('applications', views.ApplicationViewSet, 'logistics-application'),
    ('documents', views.DocumentViewSet, 'logistics-document'),
    ('requests', views.RequestViewSet, 'logistics-request'),
    ('conditions', views.ConditionViewSet, 'logistics-condition'),
    ('restrictions', views.RestrictionViewSet, 'logistics-restriction'),
    ('notifications', views.NotificationViewSet, 'logistics-notification'),
    ('alerts', views.AlertViewSet, 'logistics-alert'),
    ('reports', views.ReportViewSet, 'logistics-report'),
]:
    router.register(prefix, view, basename=basename)

urlpatterns = router.urls + [
    path(name + '/', views.SummaryViewSet.as_view({'get': name}), name='logistics-' + name)
    for name in ['me', 'dashboard', 'risk', 'audit']
]
