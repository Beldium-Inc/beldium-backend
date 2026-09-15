from rest_framework.routers import DefaultRouter

from finance.views import InvoiceViewSet, PaymentViewSet

router = DefaultRouter()
router.register("invoices", InvoiceViewSet, basename="finance-invoice")
router.register("payments", PaymentViewSet, basename="finance-payment")

urlpatterns = router.urls
