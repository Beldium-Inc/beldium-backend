from django.urls import path
from rest_framework.routers import DefaultRouter

from ecosystem.views import (
    CommitmentViewSet,
    EcosystemDashboardView,
    LogisticsMoveViewSet,
    MaterialBatchViewSet,
    RfqViewSet,
    TransactionAdvanceStageView,
    TransactionViewSet,
)

router = DefaultRouter()
router.register("rfqs", RfqViewSet, basename="ecosystem-rfq")
router.register("transactions", TransactionViewSet, basename="ecosystem-transaction")
router.register("batches", MaterialBatchViewSet, basename="ecosystem-batch")
router.register("logistics-moves", LogisticsMoveViewSet, basename="ecosystem-move")
router.register("commitments", CommitmentViewSet, basename="ecosystem-commitment")

urlpatterns = [
    path("dashboard/", EcosystemDashboardView.as_view(), name="ecosystem-dashboard"),
    path("transactions/<uuid:pk>/advance-stage/", TransactionAdvanceStageView.as_view(), name="ecosystem-transaction-advance"),
]
urlpatterns += router.urls
