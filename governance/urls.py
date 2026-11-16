from django.urls import path
from rest_framework.routers import DefaultRouter

from governance import views

router = DefaultRouter()
for prefix, view, basename in [
    ("policies", views.PolicyViewSet, "governance-policy"),
    ("risks", views.RiskViewSet, "governance-risk"),
    ("findings", views.FindingViewSet, "governance-finding"),
    ("stakeholders", views.StakeholderViewSet, "governance-stakeholder"),
    ("milestones", views.MilestoneViewSet, "governance-milestone"),
    ("assessments", views.AssessmentViewSet, "governance-assessment"),
    ("scenarios", views.ScenarioViewSet, "governance-scenario"),
    ("documents", views.DocumentViewSet, "governance-document"),
    ("members", views.MemberViewSet, "governance-member"),
    ("invitations", views.InvitationViewSet, "governance-invitation"),
]:
    router.register(prefix, view, basename=basename)

urlpatterns = router.urls + [
    path("me/", views.MeView.as_view(), name="governance-me"),
    path("people/", views.PeopleView.as_view(), name="governance-people"),
    path("dashboard/", views.DashboardView.as_view(), name="governance-dashboard"),
    path("compliance/", views.ComplianceView.as_view(), name="governance-compliance"),
    path("copilot/", views.CopilotView.as_view(), name="governance-copilot"),
    path("audit/", views.AuditView.as_view(), name="governance-audit"),
    path("reports/", views.ReportListView.as_view(), name="governance-reports"),
    path("reports/<str:kind>/", views.ReportView.as_view(), name="governance-report"),
    path("access-requests/", views.AccessRequestView.as_view(), name="governance-access-request"),
]
