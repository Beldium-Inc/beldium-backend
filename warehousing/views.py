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

from accounts.models import AccountAuditEvent
from common.exceptions import ConflictError
from warehousing import models as m, serializers as s, services
from warehousing.permissions import EDIT_ROLES, assert_editor, assert_reviewer, can_edit, can_review, warehouse_ids
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


def editable_warehouse(user, warehouse):
    assert_editor(user, warehouse)
    application = m.WarehousingApplication.objects.select_for_update().filter(warehouse=warehouse).first()
    if application:
        services.assert_state(application, services.EDITABLE)


class WarehouseOperatorViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    serializer_class = s.WarehouseOperatorSerializer
    queryset = m.WarehouseOperator.objects.none()
    http_method_names = ["get", "post", "patch", "head", "options"]
    search_fields = ["reference", "organisation__name", "organisation__registration_number", "warehouse_license_number"]

    def get_queryset(self):
        return m.WarehouseOperator.objects.filter(pk__in=warehouse_ids(self.request.user)).select_related("organisation")

    def perform_create(self, serializer):
        org = serializer.validated_data["organisation"]
        if not self.request.user.is_staff and not OrganisationMembership.objects.filter(
            user=self.request.user, organisation=org, role__in=EDIT_ROLES, is_active=True
        ).exists():
            raise PermissionDenied("You must administer this organisation to register its warehouse profile.")
        warehouse = serializer.save()
        services.audit(self.request, warehouse, "warehouse_created")

    def perform_update(self, serializer):
        editable_warehouse(self.request.user, serializer.instance)
        warehouse = serializer.save()
        services.audit(self.request, warehouse, "warehouse_updated")


class WarehouseOperatorRecordViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["warehouse"]

    def get_queryset(self):
        return self.queryset.model.objects.filter(warehouse_id__in=warehouse_ids(self.request.user)).select_related("warehouse__organisation")

    def perform_create(self, serializer):
        warehouse = serializer.validated_data["warehouse"]
        editable_warehouse(self.request.user, warehouse)
        obj = serializer.save()
        services.audit(self.request, warehouse, obj._meta.model_name + "_created", object_id=str(obj.pk))

    def perform_update(self, serializer):
        editable_warehouse(self.request.user, serializer.instance.warehouse)
        obj = serializer.save()
        services.audit(self.request, obj.warehouse, obj._meta.model_name + "_updated", object_id=str(obj.pk))


class FacilityViewSet(WarehouseOperatorRecordViewSet):
    queryset = m.Facility.objects.none()
    serializer_class = s.FacilitySerializer
    search_fields = ["name", "address", "state", "country"]
    filterset_fields = ["warehouse", "facility_type", "state", "country", "is_active"]


class StorageZoneViewSet(WarehouseOperatorRecordViewSet):
    queryset = m.StorageZone.objects.none()
    serializer_class = s.StorageZoneSerializer
    search_fields = ["name", "storage_type", "facility__name"]
    filterset_fields = ["warehouse", "facility", "storage_type", "restricted", "is_active"]


class InventoryLotViewSet(WarehouseOperatorRecordViewSet):
    queryset = m.InventoryLot.objects.none()
    serializer_class = s.InventoryLotSerializer
    search_fields = ["reference", "product_name", "batch_number", "owner_name"]
    filterset_fields = ["warehouse", "facility", "zone", "status"]


class InspectionViewSet(WarehouseOperatorRecordViewSet):
    queryset = m.Inspection.objects.none()
    serializer_class = s.InspectionSerializer
    search_fields = ["inspection_type", "inspector_name", "facility__name"]
    filterset_fields = ["warehouse", "facility", "inspection_type", "outcome"]


class GrantViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    permission_classes = [IsAdminUser]
    queryset = m.WarehousingAccessGrant.objects.all()
    serializer_class = s.GrantSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        grant = serializer.save()
        services.audit(self.request, grant.warehouse, "access_granted", user_id=str(grant.user_id), role=grant.role)

    def perform_update(self, serializer):
        grant = serializer.save()
        services.audit(self.request, grant.warehouse, "access_updated", user_id=str(grant.user_id), active=grant.is_active)


class ApplicationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.WarehousingApplication.objects.none()
    serializer_class = s.ApplicationSerializer
    filterset_fields = ["warehouse", "status", "reviewer"]

    def get_queryset(self):
        return m.WarehousingApplication.objects.filter(warehouse_id__in=warehouse_ids(self.request.user)).select_related("warehouse__organisation", "created_by").prefetch_related("sections", "conditions")

    def perform_create(self, serializer):
        warehouse = serializer.validated_data["warehouse"]
        assert_editor(self.request.user, warehouse)
        application = serializer.save(created_by=self.request.user)
        services.initialise(application)
        services.audit(self.request, warehouse, "application_created", application_id=str(application.pk))

    @extend_schema(request=s.SectionInputSerializer, responses=s.DomainSerializer, parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)])
    @action(detail=True, methods=["patch"], url_path=r"sections/(?P<key>[a-z_]+)")
    def section(self, request, pk=None, key=None):
        application = self.get_object()
        assert_editor(request.user, application.warehouse)
        services.assert_state(application, services.EDITABLE)
        section = get_object_or_404(application.sections, key=key)
        payload = s.SectionInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        section.data = payload.validated_data["data"]
        section.status, section.score = "pending", 0
        section.reviewed_by, section.reviewed_at = None, None
        section.save()
        services.audit(request, application.warehouse, "section_updated", application_id=str(application.pk), domain=key)
        return Response(s.DomainSerializer(section).data)

    @extend_schema(request=s.AssignReviewerSerializer, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"], url_path="assign-reviewer")
    def assign_reviewer(self, request, pk=None):
        application = self.get_object()
        if not can_review(request.user, application.warehouse):
            raise PermissionDenied("Only the warehousing desk can assign reviewers.")
        services.assert_state(application, {"submitted", "under_review", "awaiting_information", "conditionally_approved"})
        payload = s.AssignReviewerSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reviewer = payload.validated_data["reviewer"]
        if not can_review(reviewer, application.warehouse):
            raise PermissionDenied("The selected user has no review grant for this warehouse.")
        application.reviewer = reviewer
        application.save(update_fields=["reviewer", "updated_at"])
        services.audit(request, application.warehouse, "reviewer_assigned", reviewer_id=str(reviewer.pk))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=None, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"], url_path="start-review")
    def start_review(self, request, pk=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, {"submitted"})
        application.status = "under_review"
        application.save(update_fields=["status", "updated_at"])
        services.audit(request, application.warehouse, "review_started", application_id=str(application.pk))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=None, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        application = self.get_object()
        assert_editor(request.user, application.warehouse)
        services.assert_state(application, services.EDITABLE)
        services.require_complete(application)
        application.status, application.submitted_at = "submitted", timezone.now()
        application.save(update_fields=["status", "submitted_at", "updated_at"])
        services.audit(request, application.warehouse, "application_submitted", application_id=str(application.pk))
        services.notify(application.warehouse, "Warehousing application submitted", "An warehousing application is ready for review.")
        return Response(self.get_serializer(application).data)

    @extend_schema(request=s.DomainReviewInputSerializer, responses=s.DomainSerializer, parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"sections/(?P<key>[a-z_]+)/review")
    def review_section(self, request, pk=None, key=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        section = get_object_or_404(application.sections, key=key)
        payload = s.DomainReviewInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if data["status"] == "passed" and data["applicable"]:
            documents = application.documents.filter(domain=key, is_current=True)
            if not section.data or not documents.exists() or documents.exclude(status="verified").exists() or documents.filter(expires_on__lt=timezone.localdate()).exists():
                raise ConflictError("Verify current evidence and supply section data before sign-off.", code="domain_evidence_incomplete")
        section.status, section.score, section.applicable = data["status"], data["score"], data["applicable"]
        section.review_notes = data["notes"]
        section.reviewed_by, section.reviewed_at = request.user, timezone.now()
        section.save()
        services.audit(request, application.warehouse, "domain_reviewed", domain=key, verdict=section.status)
        return Response(s.DomainSerializer(section).data)

    @extend_schema(methods=["GET"], responses=s.DocumentSerializer(many=True))
    @extend_schema(methods=["POST"], request=s.DocumentUploadSerializer, responses={201: s.DocumentSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser], pagination_class=None)
    def documents(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(s.DocumentSerializer(application.documents.all(), many=True, context=self.context()).data)
        assert_editor(request.user, application.warehouse)
        services.assert_state(application, services.EDITABLE | {"conditionally_approved", "approved"})
        payload = s.DocumentUploadSerializer(data=request.data, context=self.context(application=application))
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        previous = application.documents.filter(document_type=data["document_type"], is_current=True).first()
        if application.status == "approved" and not previous:
            raise ConflictError("Only renewal of existing credentials is allowed after approval.", code="renewal_required")
        if previous:
            for field in ["domain", "lot", "condition"]:
                if data.get(field) != getattr(previous, field):
                    raise ConflictError("A document version must retain its domain and linked records.", code="document_identity_changed")
            previous.is_current = False
            previous.save(update_fields=["is_current"])
        document = payload.save(application=application, uploaded_by=request.user, original_name=Path(data["file"].name).name, version=previous.version + 1 if previous else 1)
        application.sections.filter(key=document.domain).update(status="pending", score=0, reviewed_at=None, reviewed_by=None)
        services.audit(request, application.warehouse, "document_uploaded", document_id=str(document.pk), version=document.version)
        return Response(s.DocumentSerializer(document, context=self.context()).data, status=201)

    @extend_schema(methods=["GET"], responses=s.InformationRequestSerializer(many=True))
    @extend_schema(methods=["POST"], request=s.InformationRequestSerializer, responses={201: s.InformationRequestSerializer})
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def requests(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(s.InformationRequestSerializer(application.requests.all(), many=True).data)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.InformationRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = payload.save(application=application, raised_by=request.user)
        application.status = "awaiting_information"
        application.save(update_fields=["status", "updated_at"])
        services.notify(application.warehouse, "Information requested", item.reason)
        services.audit(request, application.warehouse, "information_requested", request_id=str(item.pk))
        return Response(s.InformationRequestSerializer(item).data, status=201)

    @extend_schema(methods=["GET"], responses=s.ConditionSerializer(many=True))
    @extend_schema(methods=["POST"], request=s.ConditionSerializer, responses={201: s.ConditionSerializer})
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def conditions(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(s.ConditionSerializer(application.conditions.all(), many=True).data)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.ConditionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        condition = m.WarehousingCondition.objects.create(application=application, **payload.validated_data)
        services.audit(request, application.warehouse, "condition_created", condition_id=str(condition.pk))
        return Response(s.ConditionSerializer(condition).data, status=201)

    @extend_schema(request=s.DecisionSerializer, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, {"under_review", "conditionally_approved"})
        payload = s.DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        conditional = data["status"] == "conditionally_approved"
        if data["status"] != "rejected":
            services.require_approval(application, conditional)
        for condition in data.get("conditions", []):
            m.WarehousingCondition.objects.create(application=application, **condition)
        if conditional and not application.conditions.filter(cleared_at__isnull=True).exists():
            raise ConflictError("Conditional approval requires at least one outstanding condition.")
        application.status, application.rationale = data["status"], data["rationale"]
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "rationale", "reviewed_at", "updated_at"])
        services.audit(request, application.warehouse, "decision_recorded", verdict=application.status)
        services.notify(application.warehouse, "Warehousing application decision", application.status)
        return Response(self.get_serializer(application).data)

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        application = self.get_object()
        return Response({"events": audit_rows(application.warehouse_id)})


class DocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.WarehousingDocument.objects.none()
    serializer_class = s.DocumentSerializer
    filterset_fields = ["application", "domain", "status", "is_current", "lot"]
    search_fields = ["title", "document_type", "issuer", "reference"]

    def get_queryset(self):
        return m.WarehousingDocument.objects.filter(application__warehouse_id__in=warehouse_ids(self.request.user))

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

    @extend_schema(request=s.ReviewDocumentInputSerializer, responses=s.DocumentSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        document = self.get_object()
        application = m.WarehousingApplication.objects.select_for_update().get(pk=document.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        if not document.is_current or document.status != "pending":
            raise ConflictError("Only a current pending evidence version can be reviewed.")
        payload = s.ReviewDocumentInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data["status"] == "verified" and document.expires_on and document.expires_on < timezone.localdate():
            raise ConflictError("Expired evidence cannot be verified.")
        document.status = payload.validated_data["status"]
        document.review_notes = payload.validated_data["notes"]
        document.reviewed_by, document.reviewed_at = request.user, timezone.now()
        document.save(update_fields=["status", "review_notes", "reviewed_by", "reviewed_at", "updated_at"])
        services.audit(request, application.warehouse, "document_reviewed", document_id=str(document.pk), verdict=document.status)
        return Response(self.get_serializer(document).data)


class RequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.InformationRequest.objects.none()
    serializer_class = s.InformationRequestSerializer
    filterset_fields = ["application", "status"]

    def get_queryset(self):
        return m.InformationRequest.objects.filter(application__warehouse_id__in=warehouse_ids(self.request.user))

    @extend_schema(request=s.RequestResponseSerializer, responses={201: s.RequestResponseSerializer})
    @action(detail=True, methods=["post"])
    def responses(self, request, pk=None):
        item = self.get_object()
        application = m.WarehousingApplication.objects.select_for_update().get(pk=item.application_id)
        assert_editor(request.user, application.warehouse)
        services.assert_state(application, {"awaiting_information"})
        if item.status != "open":
            raise ConflictError("This request is not open for a response.")
        payload = s.RequestResponseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        documents = payload.validated_data.get("documents", [])
        if not documents or any(doc.application_id != application.pk or not doc.is_current or doc.status == "rejected" for doc in documents):
            raise ConflictError("Attach current evidence from this application.")
        response = payload.save(request=item, author=request.user)
        item.status = "responded"
        item.save(update_fields=["status", "updated_at"])
        services.audit(request, application.warehouse, "request_responded", request_id=str(item.pk))
        return Response(s.RequestResponseSerializer(response).data, status=201)

    @extend_schema(request=s.ResponseReviewSerializer, responses=s.InformationRequestSerializer)
    @action(detail=True, methods=["post"], url_path="review-response")
    def review_response(self, request, pk=None):
        item = self.get_object()
        application = m.WarehousingApplication.objects.select_for_update().get(pk=item.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        if item.status != "responded":
            raise ConflictError("There is no unreviewed response.")
        payload = s.ResponseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item.status = "accepted" if payload.validated_data["accepted"] else "open"
        item.review_notes = payload.validated_data["notes"]
        item.save(update_fields=["status", "review_notes", "updated_at"])
        services.audit(request, application.warehouse, "request_response_reviewed", request_id=str(item.pk), verdict=item.status)
        return Response(self.get_serializer(item).data)


class ConditionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.WarehousingCondition.objects.none()
    serializer_class = s.ConditionSerializer
    filterset_fields = ["application", "domain"]

    def get_queryset(self):
        return m.WarehousingCondition.objects.filter(application__warehouse_id__in=warehouse_ids(self.request.user))

    @extend_schema(request=s.ResolutionSerializer, responses=s.ConditionSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        condition = self.get_object()
        application = m.WarehousingApplication.objects.select_for_update().get(pk=condition.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.ResolutionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        documents = condition.documents.filter(is_current=True)
        if condition.cleared_at or not documents.exists() or documents.exclude(status="verified").exists() or documents.filter(expires_on__lt=timezone.localdate()).exists():
            raise ConflictError("An outstanding condition requires current verified evidence before clearance.")
        condition.cleared_at, condition.cleared_by = timezone.now(), request.user
        condition.save(update_fields=["cleared_at", "cleared_by", "updated_at"])
        services.audit(request, application.warehouse, "condition_cleared", condition_id=str(condition.pk), notes=payload.validated_data["notes"])
        return Response(self.get_serializer(condition).data)


class NotificationViewSet(mixins.ListModelMixin, AtomicViewSet):
    queryset = m.Notification.objects.none()
    serializer_class = s.NotificationSerializer

    def get_queryset(self):
        return m.Notification.objects.filter(recipient=self.request.user, warehouse_id__in=warehouse_ids(self.request.user))

    @extend_schema(request=None, responses=s.NotificationSerializer)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)


def audit_rows(warehouse_id):
    return list(AccountAuditEvent.objects.filter(event_type__startswith="warehousing.", metadata__warehouse_id=str(warehouse_id)).order_by("-created_at").values("id", "event_type", "created_at", "actor_id")[:200])


class SummaryViewSet(AtomicViewSet):
    serializer_class = s.SummarySerializer

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def me(self, request):
        warehouses = m.WarehouseOperator.objects.filter(pk__in=warehouse_ids(request.user)).select_related("organisation")
        return Response({"is_staff": request.user.is_staff, "warehouses": [
            {"id": warehouse.pk, "name": warehouse.organisation.name, "can_edit": can_edit(request.user, warehouse), "can_review": can_review(request.user, warehouse), "can_read": True}
            for warehouse in warehouses
        ]})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        warehouses = m.WarehouseOperator.objects.filter(pk__in=warehouse_ids(request.user)).select_related("organisation")
        data = []
        totals = {"facilities": 0, "zones": 0, "lots": 0, "open_requests": 0, "expiring_documents": 0}
        for warehouse in warehouses:
            application = m.WarehousingApplication.objects.filter(warehouse=warehouse).first()
            facilities = warehouse.facilities.filter(is_active=True).count()
            zones = warehouse.zones.filter(is_active=True).count()
            lots = warehouse.lots.exclude(status__in=["dispatched"]).count()
            open_requests = application.requests.exclude(status="accepted").count() if application else 0
            expiring = application.documents.filter(is_current=True, expires_on__lte=timezone.localdate() + timedelta(days=30)).count() if application else 0
            totals["facilities"] += facilities
            totals["zones"] += zones
            totals["lots"] += lots
            totals["open_requests"] += open_requests
            totals["expiring_documents"] += expiring
            data.append({
                "warehouse_id": warehouse.pk,
                "reference": warehouse.reference,
                "name": warehouse.organisation.name,
                "application_id": application.pk if application else None,
                "status": application.status if application else "not_started",
                "progress": services.progress(application) if application else None,
                "risk": services.risk(application) if application else None,
                "facilities": facilities,
                "zones": zones,
                "lots": lots,
                "open_requests": open_requests,
                "expiring_documents": expiring,
            })
        return Response({"warehouses": data, "warehouse_count": len(data), "totals": totals, "unread_notifications": m.Notification.objects.filter(recipient=request.user, warehouse__in=warehouses, read_at__isnull=True).count()})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def risk(self, request):
        applications = m.WarehousingApplication.objects.filter(warehouse_id__in=warehouse_ids(request.user))
        rows = [{"warehouse_id": app.warehouse_id, "application_id": app.pk, **services.risk(app)} for app in applications]
        return Response({"applications": sorted(rows, key=lambda row: row["compliance_score"])})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def audit(self, request):
        rows = []
        for pk in warehouse_ids(request.user):
            rows.extend(audit_rows(pk))
        return Response({"events": sorted(rows, key=lambda row: row["created_at"], reverse=True)[:200]})


class ReportViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.WarehousingReport.objects.none()
    serializer_class = s.ReportSerializer

    def get_queryset(self):
        return m.WarehousingReport.objects.filter(requested_by=self.request.user)

    @extend_schema(request=None, responses={201: s.ReportSerializer})
    def create(self, request, *args, **kwargs):
        warehouses = m.WarehouseOperator.objects.filter(pk__in=warehouse_ids(request.user)).select_related("organisation")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Reference", "WarehouseOperator", "Application status", "Compliance score", "Risk band", "Facilitys", "StorageZones", "InventoryLots"])
        ids = []
        for warehouse in warehouses:
            ids.append(str(warehouse.pk))
            application = m.WarehousingApplication.objects.filter(warehouse=warehouse).first()
            score = services.risk(application) if application else {}
            name = warehouse.organisation.name
            if name.lstrip().startswith(("=", "+", "-", "@")):
                name = "'" + name
            writer.writerow([warehouse.reference, name, application.status if application else "not_started", score.get("compliance_score", ""), score.get("risk_band", ""), warehouse.facilities.filter(is_active=True).count(), warehouse.zones.filter(is_active=True).count(), warehouse.lots.exclude(status__in=["dispatched"]).count()])
            services.audit(request, warehouse, "register_warehousinged")
        report = m.WarehousingReport.objects.create(requested_by=request.user, warehouse_ids=ids, content=output.getvalue())
        return Response(self.get_serializer(report).data, status=201)

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        report = self.get_object()
        if not set(report.warehouse_ids).issubset({str(pk) for pk in warehouse_ids(request.user)}):
            raise PermissionDenied("Your access to this report has changed; generate a new report.")
        response = HttpResponse(report.content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="warehousing-register-{report.pk}.csv"'
        response["Cache-Control"] = "private, no-store"
        return response
