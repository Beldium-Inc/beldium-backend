import csv
import io
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from common.exceptions import ConflictError
from marketplace import models as m, serializers as s, services
from marketplace.permissions import assert_editor, assert_reviewer, can_edit, can_review, seller_ids, EDIT_ROLES
from organisations.models import OrganisationMembership


class AtomicViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        result = super().dispatch(request, *args, **kwargs)
        if result.status_code >= 400:
            transaction.set_rollback(True)
        return result

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            obj = type(obj).objects.select_for_update().get(pk=obj.pk)
        return obj

    def context(self, **extra):
        return {**self.get_serializer_context(), **extra}


class SellerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    queryset = m.SellerProfile.objects.none()
    serializer_class = s.SellerSerializer
    filterset_fields = ["status"]
    search_fields = ["display_name", "reference", "organisation__name", "organisation__registration_number"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return m.SellerProfile.objects.filter(pk__in=seller_ids(self.request.user)).select_related("organisation")

    def perform_create(self, serializer):
        org = serializer.validated_data["organisation"]
        if not self.request.user.is_staff and not OrganisationMembership.objects.filter(
            organisation=org,
            user=self.request.user,
            is_active=True,
            role__in=EDIT_ROLES,
        ).exists():
            raise PermissionDenied("You must administer this organisation to create a marketplace seller profile.")
        seller = serializer.save()
        services.audit(self.request, "seller_created", seller=seller)

    def perform_update(self, serializer):
        assert_editor(self.request.user, serializer.instance)
        seller = serializer.save()
        services.audit(self.request, "seller_updated", seller=seller)

    @extend_schema(request=None, responses=s.SellerSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        seller = self.get_object()
        assert_editor(request.user, seller)
        if seller.status not in {m.SellerStatus.DRAFT, m.SellerStatus.REJECTED, m.SellerStatus.RESTRICTED}:
            raise ConflictError("This seller profile is not editable for submission.", code="invalid_seller_state")
        seller.status = m.SellerStatus.PENDING_REVIEW
        seller.accepted_terms_at = seller.accepted_terms_at or timezone.now()
        seller.save(update_fields=["status", "accepted_terms_at", "updated_at"])
        services.audit(request, "seller_submitted", seller=seller)
        return Response(self.get_serializer(seller).data)

    @extend_schema(request=s.SellerDecisionSerializer, responses=s.SellerSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        seller = self.get_object()
        assert_reviewer(request.user, seller)
        payload = s.SellerDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        seller.status = payload.validated_data["status"]
        seller.review_notes = payload.validated_data.get("notes", "")
        seller.trust_score = payload.validated_data.get("trust_score", seller.trust_score)
        seller.reviewed_by = request.user
        seller.reviewed_at = timezone.now()
        seller.save(update_fields=["status", "review_notes", "trust_score", "reviewed_by", "reviewed_at", "updated_at"])
        services.audit(request, "seller_decided", seller=seller, verdict=seller.status)
        services.notify(services.seller_recipients(seller), "Seller review decision", seller.status, seller=seller)
        return Response(self.get_serializer(seller).data)


class AccessGrantViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    permission_classes = [IsAdminUser]
    queryset = m.MarketplaceAccessGrant.objects.all()
    serializer_class = s.AccessGrantSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]


class ProductViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    queryset = m.MarketplaceProduct.objects.none()
    serializer_class = s.ProductSerializer
    filterset_fields = ["seller", "status", "category", "risk_band"]
    search_fields = ["name", "reference", "mineral_type", "origin_state"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        if self.request.user.is_authenticated and self.request.user.is_staff:
            return m.MarketplaceProduct.objects.all().select_related("seller__organisation").prefetch_related("checks")
        visible = Q(status=m.ListingStatus.ACTIVE) | Q(seller_id__in=seller_ids(self.request.user))
        return m.MarketplaceProduct.objects.filter(visible).distinct().select_related("seller__organisation").prefetch_related("checks")

    def perform_create(self, serializer):
        seller = serializer.validated_data["seller"]
        assert_editor(self.request.user, seller)
        product = serializer.save()
        services.initialise_product(product)
        services.audit(self.request, "product_created", seller=seller, product=product)

    def perform_update(self, serializer):
        assert_editor(self.request.user, serializer.instance.seller)
        if serializer.instance.status not in {m.ListingStatus.DRAFT, m.ListingStatus.CHANGES_REQUIRED, m.ListingStatus.RESTRICTED}:
            raise ConflictError("This listing cannot be edited in its current state.", code="invalid_listing_state")
        product = serializer.save(status=m.ListingStatus.DRAFT)
        services.refresh_product_risk(product)
        services.audit(self.request, "product_updated", seller=product.seller, product=product)

    @extend_schema(request=None, responses=s.ProductSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        product = self.get_object()
        assert_editor(request.user, product.seller)
        if product.seller.status not in {m.SellerStatus.VERIFIED, m.SellerStatus.RESTRICTED}:
            raise ConflictError("The seller must be reviewed before listings can be submitted.", code="seller_not_verified")
        services.require_listing_ready(product)
        product.status = m.ListingStatus.PENDING_REVIEW
        product.save(update_fields=["status", "updated_at"])
        services.audit(request, "product_submitted", seller=product.seller, product=product)
        return Response(self.get_serializer(product).data)

    @extend_schema(request=s.ProductReviewSerializer, responses=s.ProductSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        product = self.get_object()
        assert_reviewer(request.user, product.seller)
        payload = s.ProductReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        product.status = payload.validated_data["status"]
        product.review_notes = payload.validated_data.get("notes", "")
        product.reviewed_by = request.user
        product.reviewed_at = timezone.now()
        if product.status == m.ListingStatus.ACTIVE:
            product.published_at = product.published_at or timezone.now()
        product.save(update_fields=["status", "review_notes", "reviewed_by", "reviewed_at", "published_at", "updated_at"])
        services.audit(request, "product_reviewed", seller=product.seller, product=product, verdict=product.status)
        return Response(self.get_serializer(product).data)

    @extend_schema(methods=["GET"], responses=s.DocumentSerializer(many=True))
    @extend_schema(methods=["POST"], request=s.DocumentUploadSerializer, responses={201: s.DocumentSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser], pagination_class=None)
    def documents(self, request, pk=None):
        product = self.get_object()
        if request.method == "GET":
            return Response(s.DocumentSerializer(product.documents.all(), many=True, context=self.context()).data)
        assert_editor(request.user, product.seller)
        payload = s.DocumentUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        previous = product.documents.filter(document_type=data["document_type"], is_current=True).first()
        if previous:
            previous.is_current = False
            previous.save(update_fields=["is_current"])
        document = payload.save(
            product=product,
            uploaded_by=request.user,
            original_name=Path(data["file"].name).name,
            version=previous.version + 1 if previous else 1,
        )
        product.status = m.ListingStatus.DRAFT if product.status != m.ListingStatus.DRAFT else product.status
        product.save(update_fields=["status", "updated_at"])
        services.refresh_product_risk(product)
        services.audit(request, "document_uploaded", seller=product.seller, product=product, document_id=str(document.pk))
        return Response(s.DocumentSerializer(document, context=self.context()).data, status=201)

    @extend_schema(request=s.ComplianceCheckReviewSerializer, responses=s.ComplianceCheckSerializer, parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"checks/(?P<key>[a-z_]+)")
    def review_check(self, request, pk=None, key=None):
        product = self.get_object()
        assert_reviewer(request.user, product.seller)
        check = get_object_or_404(product.checks, key=key)
        payload = s.ComplianceCheckReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        check.status = payload.validated_data["status"]
        check.score = payload.validated_data["score"]
        check.notes = payload.validated_data.get("notes", "")
        check.reviewed_by = request.user
        check.reviewed_at = timezone.now()
        check.save(update_fields=["status", "score", "notes", "reviewed_by", "reviewed_at", "updated_at"])
        services.refresh_product_risk(product)
        services.audit(request, "check_reviewed", seller=product.seller, product=product, key=key)
        return Response(s.ComplianceCheckSerializer(check).data)


class DocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.ProductDocument.objects.none()
    serializer_class = s.DocumentSerializer
    filterset_fields = ["product", "status", "document_type", "is_current"]
    search_fields = ["title", "issuer", "reference"]

    def get_queryset(self):
        visible_products = m.MarketplaceProduct.objects.filter(Q(status=m.ListingStatus.ACTIVE) | Q(seller_id__in=seller_ids(self.request.user)))
        return m.ProductDocument.objects.filter(product__in=visible_products).select_related("product__seller")

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        document = self.get_object()
        response = FileResponse(document.file.open("rb"), as_attachment=True, filename=document.original_name)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

    @extend_schema(responses=s.DocumentSerializer(many=True))
    @action(detail=False, methods=["get"], pagination_class=None)
    def expiring(self, request):
        items = self.get_queryset().filter(is_current=True, expires_on__lte=timezone.localdate() + timedelta(days=30))
        return Response(self.get_serializer(items, many=True).data)

    @extend_schema(request=s.DocumentReviewSerializer, responses=s.DocumentSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        document = self.get_object()
        assert_reviewer(request.user, document.product.seller)
        payload = s.DocumentReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data["status"] == "verified" and document.expires_on and document.expires_on < timezone.localdate():
            raise ConflictError("Expired evidence cannot be verified.", code="expired_evidence")
        document.status = payload.validated_data["status"]
        document.review_notes = payload.validated_data.get("notes", "")
        document.reviewed_by = request.user
        document.reviewed_at = timezone.now()
        document.save(update_fields=["status", "review_notes", "reviewed_by", "reviewed_at", "updated_at"])
        services.refresh_product_risk(document.product)
        services.audit(request, "document_reviewed", seller=document.product.seller, product=document.product, document_id=str(document.pk))
        return Response(self.get_serializer(document).data)


class OrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.MarketplaceOrder.objects.none()
    serializer_class = s.OrderSerializer
    filterset_fields = ["seller", "product", "status", "payment_status"]

    def get_queryset(self):
        return m.MarketplaceOrder.objects.filter(Q(buyer=self.request.user) | Q(seller_id__in=seller_ids(self.request.user))).select_related("seller", "product", "buyer")

    def perform_create(self, serializer):
        order = serializer.save(buyer=self.request.user, unit_price=serializer.validated_data["product"].price, currency=serializer.validated_data["product"].currency)
        services.audit(self.request, "order_created", seller=order.seller, product=order.product, order=order)
        services.notify(services.seller_recipients(order.seller), "Marketplace order received", order.reference, seller=order.seller)

    @extend_schema(request=s.FulfillOrderSerializer, responses=s.OrderSerializer)
    @action(detail=True, methods=["post"])
    def fulfill(self, request, pk=None):
        order = self.get_object()
        assert_editor(request.user, order.seller)
        if order.payment_status != m.PaymentStatus.PAID:
            raise ConflictError("Only paid orders can be fulfilled.", code="order_not_paid")
        payload = s.FulfillOrderSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        order.status = m.OrderStatus.SHIPPED
        order.shipping_reference = payload.validated_data["shipping_reference"]
        order.fulfilled_at = timezone.now()
        order.save(update_fields=["status", "shipping_reference", "fulfilled_at", "updated_at"])
        services.audit(request, "order_fulfilled", seller=order.seller, product=order.product, order=order)
        return Response(self.get_serializer(order).data)


class PaymentWebhookViewSet(AtomicViewSet):
    serializer_class = s.PaymentWebhookSerializer

    @extend_schema(request=s.PaymentWebhookSerializer, responses=s.SummarySerializer)
    @action(detail=False, methods=["post"], permission_classes=[IsAuthenticated])
    def receive(self, request):
        payload = self.get_serializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        event, created = m.PaymentWebhookEvent.objects.get_or_create(
            provider=data["provider"],
            event_id=data["event_id"],
            defaults={"event_type": data["event_type"], "payload": data.get("payload", {})},
        )
        order = get_object_or_404(m.MarketplaceOrder, payment_reference=data["payment_reference"])
        if created:
            order.payment_status = data["status"]
            if data["status"] == m.PaymentStatus.PAID:
                order.status = m.OrderStatus.PAID
            order.save(update_fields=["payment_status", "status", "updated_at"])
            event.processed_at = timezone.now()
            event.save(update_fields=["processed_at"])
            services.audit(request, "payment_webhook_processed", seller=order.seller, product=order.product, order=order, event_id=data["event_id"])
        return Response({"processed": created, "order": str(order.pk), "payment_status": order.payment_status})


class LicenseViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    queryset = m.MarketplaceLicense.objects.none()
    serializer_class = s.LicenseSerializer
    filterset_fields = ["seller", "product", "status"]

    def get_queryset(self):
        return m.MarketplaceLicense.objects.filter(seller_id__in=seller_ids(self.request.user))

    def perform_create(self, serializer):
        seller = serializer.validated_data["seller"]
        assert_editor(self.request.user, seller)
        serializer.save()

    def perform_update(self, serializer):
        assert_editor(self.request.user, serializer.instance.seller)
        serializer.save()


class DisputeViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.MarketplaceDispute.objects.none()
    serializer_class = s.DisputeSerializer
    filterset_fields = ["status", "order"]

    def get_queryset(self):
        return m.MarketplaceDispute.objects.filter(Q(order__buyer=self.request.user) | Q(order__seller_id__in=seller_ids(self.request.user)))

    def perform_create(self, serializer):
        order = serializer.validated_data["order"]
        if order.buyer_id != self.request.user.pk and order.seller_id not in set(seller_ids(self.request.user)):
            raise PermissionDenied("You cannot dispute this order.")
        dispute = serializer.save(raised_by=self.request.user)
        order.status = m.OrderStatus.DISPUTED
        order.save(update_fields=["status", "updated_at"])
        services.audit(self.request, "dispute_created", seller=order.seller, product=order.product, order=order, dispute_id=str(dispute.pk))

    @extend_schema(request=s.DisputeResolutionSerializer, responses=s.DisputeSerializer)
    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        dispute = self.get_object()
        assert_reviewer(request.user, dispute.order.seller)
        payload = s.DisputeResolutionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        dispute.status = "resolved"
        dispute.resolution = payload.validated_data["resolution"]
        dispute.resolved_by = request.user
        dispute.resolved_at = timezone.now()
        dispute.save(update_fields=["status", "resolution", "resolved_by", "resolved_at", "updated_at"])
        services.audit(request, "dispute_resolved", seller=dispute.order.seller, product=dispute.order.product, order=dispute.order, dispute_id=str(dispute.pk))
        return Response(self.get_serializer(dispute).data)


class NotificationViewSet(mixins.ListModelMixin, AtomicViewSet):
    queryset = m.MarketplaceNotification.objects.none()
    serializer_class = s.NotificationSerializer

    def get_queryset(self):
        return m.MarketplaceNotification.objects.filter(recipient=self.request.user)

    @extend_schema(request=None, responses=s.NotificationSerializer)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)


class AuditViewSet(mixins.ListModelMixin, AtomicViewSet):
    queryset = m.MarketplaceAuditEvent.objects.none()
    serializer_class = s.AuditSerializer

    def get_queryset(self):
        return m.MarketplaceAuditEvent.objects.filter(Q(seller_id__in=seller_ids(self.request.user)) | Q(actor=self.request.user))


class ReportViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.MarketplaceReport.objects.none()
    serializer_class = s.ReportSerializer

    def get_queryset(self):
        return m.MarketplaceReport.objects.filter(requested_by=self.request.user)

    @extend_schema(request=None, responses={201: s.ReportSerializer})
    def create(self, request, *args, **kwargs):
        sellers = m.SellerProfile.objects.filter(pk__in=seller_ids(request.user))
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Seller", "Products", "Active listings", "Open orders", "Open disputes"])
        for seller in sellers:
            writer.writerow([
                seller.display_name,
                seller.products.count(),
                seller.products.filter(status=m.ListingStatus.ACTIVE).count(),
                seller.orders.exclude(status__in=[m.OrderStatus.COMPLETED, m.OrderStatus.CANCELLED]).count(),
                m.MarketplaceDispute.objects.filter(order__seller=seller).exclude(status="resolved").count(),
            ])
        report = m.MarketplaceReport.objects.create(requested_by=request.user, content=output.getvalue())
        return Response(self.get_serializer(report).data, status=201)

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        report = self.get_object()
        response = HttpResponse(report.content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="marketplace-report-{report.pk}.csv"'
        response["Cache-Control"] = "private, no-store"
        return response


class SummaryViewSet(AtomicViewSet):
    serializer_class = s.SummarySerializer

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def me(self, request):
        sellers = m.SellerProfile.objects.filter(pk__in=seller_ids(request.user)).select_related("organisation")
        return Response({"is_staff": request.user.is_staff, "sellers": [
            {"id": seller.pk, "name": seller.display_name, "status": seller.status, "can_edit": can_edit(request.user, seller), "can_review": can_review(request.user, seller), "can_read": True}
            for seller in sellers
        ]})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        sellers = m.SellerProfile.objects.filter(pk__in=seller_ids(request.user)).select_related("organisation")
        rows = []
        for seller in sellers:
            rows.append({
                "seller_id": seller.pk,
                "name": seller.display_name,
                "status": seller.status,
                "trust_score": seller.trust_score,
                "products": seller.products.count(),
                "active_listings": seller.products.filter(status=m.ListingStatus.ACTIVE).count(),
                "pending_reviews": seller.products.filter(status=m.ListingStatus.PENDING_REVIEW).count(),
                "open_orders": seller.orders.exclude(status__in=[m.OrderStatus.COMPLETED, m.OrderStatus.CANCELLED]).count(),
                "open_disputes": m.MarketplaceDispute.objects.filter(order__seller=seller).exclude(status="resolved").count(),
            })
        return Response({"sellers": rows, "totals": services.dashboard_for(sellers), "unread_notifications": m.MarketplaceNotification.objects.filter(recipient=request.user, read_at__isnull=True).count()})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def risk(self, request):
        products = m.MarketplaceProduct.objects.filter(seller_id__in=seller_ids(request.user)).select_related("seller")
        return Response({"products": [
            {"product_id": product.pk, "seller_id": product.seller_id, "name": product.name, "status": product.status, "compliance_score": product.compliance_score, "risk_band": product.risk_band}
            for product in products.order_by("compliance_score")
        ]})
