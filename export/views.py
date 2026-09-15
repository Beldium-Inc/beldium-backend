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
from export import models as m, serializers as s, services
from export.permissions import EDIT_ROLES, assert_editor, assert_reviewer, can_edit, can_review, exporter_ids
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


def editable_exporter(user, exporter):
    assert_editor(user, exporter)
    application = m.ExportApplication.objects.select_for_update().filter(exporter=exporter).first()
    if application:
        services.assert_state(application, services.EDITABLE)


class ExporterViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    serializer_class = s.ExporterSerializer
    queryset = m.Exporter.objects.none()
    http_method_names = ["get", "post", "patch", "head", "options"]
    search_fields = ["reference", "organisation__name", "organisation__registration_number", "export_license_number"]

    def get_queryset(self):
        return m.Exporter.objects.filter(pk__in=exporter_ids(self.request.user)).select_related("organisation")

    def perform_create(self, serializer):
        org = serializer.validated_data["organisation"]
        if not self.request.user.is_staff and not OrganisationMembership.objects.filter(
            user=self.request.user, organisation=org, role__in=EDIT_ROLES, is_active=True
        ).exists():
            raise PermissionDenied("You must administer this organisation to register its exporter profile.")
        exporter = serializer.save()
        services.audit(self.request, exporter, "exporter_created")

    def perform_update(self, serializer):
        editable_exporter(self.request.user, serializer.instance)
        exporter = serializer.save()
        services.audit(self.request, exporter, "exporter_updated")


class ExporterRecordViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["exporter"]

    def get_queryset(self):
        return self.queryset.model.objects.filter(exporter_id__in=exporter_ids(self.request.user)).select_related("exporter__organisation")

    def perform_create(self, serializer):
        exporter = serializer.validated_data["exporter"]
        editable_exporter(self.request.user, exporter)
        obj = serializer.save()
        services.audit(self.request, exporter, obj._meta.model_name + "_created", object_id=str(obj.pk))

    def perform_update(self, serializer):
        editable_exporter(self.request.user, serializer.instance.exporter)
        obj = serializer.save()
        services.audit(self.request, obj.exporter, obj._meta.model_name + "_updated", object_id=str(obj.pk))


class ProductViewSet(ExporterRecordViewSet):
    queryset = m.Product.objects.none()
    serializer_class = s.ProductSerializer
    search_fields = ["name", "hs_code"]
    filterset_fields = ["exporter", "is_active", "controlled"]


class BuyerViewSet(ExporterRecordViewSet):
    queryset = m.Buyer.objects.none()
    serializer_class = s.BuyerSerializer
    search_fields = ["name", "country", "tax_identifier"]
    filterset_fields = ["exporter", "country", "screening_status", "is_active"]


class ShipmentViewSet(ExporterRecordViewSet):
    queryset = m.Shipment.objects.none()
    serializer_class = s.ShipmentSerializer
    search_fields = ["reference", "destination_country", "port_of_loading", "port_of_discharge"]
    filterset_fields = ["exporter", "product", "buyer", "destination_country", "status"]

    def get_queryset(self):
        return super().get_queryset().prefetch_related("checklist", "non_conformities")

    @extend_schema(methods=["GET"], responses=s.ShipmentChecklistItemSerializer(many=True))
    @extend_schema(methods=["POST"], request=s.ShipmentChecklistItemSerializer, responses={201: s.ShipmentChecklistItemSerializer})
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def checklist(self, request, pk=None):
        shipment = self.get_object()
        if request.method == "GET":
            return Response(s.ShipmentChecklistItemSerializer(shipment.checklist.all(), many=True).data)
        editable_exporter(request.user, shipment.exporter)
        payload = s.ShipmentChecklistItemSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = payload.save(shipment=shipment)
        services.audit(request, shipment.exporter, "shipment_checklist_item_created", shipment_id=str(shipment.pk))
        return Response(s.ShipmentChecklistItemSerializer(item).data, status=201)

    @extend_schema(request=s.ShipmentChecklistStateSerializer, responses=s.ShipmentChecklistItemSerializer, parameters=[OpenApiParameter("item_id", OpenApiTypes.INT, OpenApiParameter.PATH)])
    @action(detail=True, methods=["patch"], url_path=r"checklist/(?P<item_id>[0-9]+)")
    def checklist_item(self, request, pk=None, item_id=None):
        shipment = self.get_object()
        editable_exporter(request.user, shipment.exporter)
        item = get_object_or_404(shipment.checklist, pk=item_id)
        payload = s.ShipmentChecklistStateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item.state = payload.validated_data["state"]
        item.save(update_fields=["state", "updated_at"])
        services.audit(request, shipment.exporter, "shipment_checklist_item_updated", shipment_id=str(shipment.pk), state=item.state)
        return Response(s.ShipmentChecklistItemSerializer(item).data)

    @extend_schema(methods=["GET"], responses=s.ShipmentNonConformitySerializer(many=True))
    @extend_schema(methods=["POST"], request=s.ShipmentNonConformitySerializer, responses={201: s.ShipmentNonConformitySerializer})
    @action(detail=True, methods=["get", "post"], pagination_class=None, url_path="non-conformities")
    def non_conformities(self, request, pk=None):
        shipment = self.get_object()
        if request.method == "GET":
            return Response(s.ShipmentNonConformitySerializer(shipment.non_conformities.all(), many=True).data)
        editable_exporter(request.user, shipment.exporter)
        payload = s.ShipmentNonConformitySerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = payload.save(shipment=shipment, raised_by=request.user)
        services.audit(request, shipment.exporter, "non_conformity_raised", shipment_id=str(shipment.pk), non_conformity_id=str(item.pk))
        return Response(s.ShipmentNonConformitySerializer(item).data, status=201)

    @extend_schema(request=s.ShipmentNonConformityRespondSerializer, responses=s.ShipmentNonConformitySerializer, parameters=[OpenApiParameter("nc_id", OpenApiTypes.INT, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"non-conformities/(?P<nc_id>[0-9]+)/respond")
    def respond_non_conformity(self, request, pk=None, nc_id=None):
        shipment = self.get_object()
        nc = get_object_or_404(shipment.non_conformities, pk=nc_id)
        if nc.status == "closed":
            raise ConflictError("This finding is already closed.")
        payload = s.ShipmentNonConformityRespondSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        nc.status, nc.response = "responded", payload.validated_data["response"]
        nc.save(update_fields=["status", "response", "updated_at"])
        services.audit(request, shipment.exporter, "non_conformity_responded", shipment_id=str(shipment.pk), non_conformity_id=str(nc.pk))
        return Response(s.ShipmentNonConformitySerializer(nc).data)

    @extend_schema(request=s.ShipmentNonConformityCloseSerializer, responses=s.ShipmentNonConformitySerializer, parameters=[OpenApiParameter("nc_id", OpenApiTypes.INT, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"non-conformities/(?P<nc_id>[0-9]+)/close")
    def close_non_conformity(self, request, pk=None, nc_id=None):
        shipment = self.get_object()
        editable_exporter(request.user, shipment.exporter)
        nc = get_object_or_404(shipment.non_conformities, pk=nc_id)
        nc.status = "closed"
        nc.save(update_fields=["status", "updated_at"])
        services.audit(request, shipment.exporter, "non_conformity_closed", shipment_id=str(shipment.pk), non_conformity_id=str(nc.pk))
        return Response(s.ShipmentNonConformitySerializer(nc).data)

    @extend_schema(request=s.ShipmentDecisionSerializer, responses=s.ShipmentSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        shipment = self.get_object()
        editable_exporter(request.user, shipment.exporter)
        if shipment.decision_outcome:
            raise ConflictError("A decision has already been recorded for this shipment.")
        blocking = shipment.checklist.filter(state="fail").count()
        open_nc = shipment.non_conformities.exclude(status="closed").count()
        payload = s.ShipmentDecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if data["outcome"] == "cleared" and (blocking or open_nc):
            raise ConflictError("Resolve blocking checklist items and open non-conformities before clearance.")
        shipment.decision_outcome = data["outcome"]
        shipment.decision_rationale = data["rationale"]
        shipment.decision_conditions = data.get("conditions", "")
        shipment.decision_by, shipment.decision_at = request.user, timezone.now()
        shipment.status = "cleared" if data["outcome"] != "declined" else "held"
        shipment.save(update_fields=["decision_outcome", "decision_rationale", "decision_conditions", "decision_by", "decision_at", "status", "updated_at"])
        services.audit(request, shipment.exporter, "shipment_decided", shipment_id=str(shipment.pk), verdict=shipment.decision_outcome)
        return Response(self.get_serializer(shipment).data)


class GrantViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    permission_classes = [IsAdminUser]
    queryset = m.ExportAccessGrant.objects.all()
    serializer_class = s.GrantSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        grant = serializer.save()
        services.audit(self.request, grant.exporter, "access_granted", user_id=str(grant.user_id), role=grant.role)

    def perform_update(self, serializer):
        grant = serializer.save()
        services.audit(self.request, grant.exporter, "access_updated", user_id=str(grant.user_id), active=grant.is_active)


class ApplicationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.ExportApplication.objects.none()
    serializer_class = s.ApplicationSerializer
    filterset_fields = ["exporter", "status", "reviewer"]

    def get_queryset(self):
        return m.ExportApplication.objects.filter(exporter_id__in=exporter_ids(self.request.user)).select_related("exporter__organisation", "created_by").prefetch_related("sections", "conditions")

    def perform_create(self, serializer):
        exporter = serializer.validated_data["exporter"]
        assert_editor(self.request.user, exporter)
        application = serializer.save(created_by=self.request.user)
        services.initialise(application)
        services.audit(self.request, exporter, "application_created", application_id=str(application.pk))

    @extend_schema(request=s.SectionInputSerializer, responses=s.DomainSerializer, parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)])
    @action(detail=True, methods=["patch"], url_path=r"sections/(?P<key>[a-z_]+)")
    def section(self, request, pk=None, key=None):
        application = self.get_object()
        assert_editor(request.user, application.exporter)
        services.assert_state(application, services.EDITABLE)
        section = get_object_or_404(application.sections, key=key)
        payload = s.SectionInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        section.data = payload.validated_data["data"]
        section.status, section.score = "pending", 0
        section.reviewed_by, section.reviewed_at = None, None
        section.save()
        services.audit(request, application.exporter, "section_updated", application_id=str(application.pk), domain=key)
        return Response(s.DomainSerializer(section).data)

    @extend_schema(request=s.AssignReviewerSerializer, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"], url_path="assign-reviewer")
    def assign_reviewer(self, request, pk=None):
        application = self.get_object()
        if not can_review(request.user, application.exporter):
            raise PermissionDenied("Only the export desk can assign reviewers.")
        services.assert_state(application, {"submitted", "under_review", "awaiting_information", "conditionally_approved"})
        payload = s.AssignReviewerSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reviewer = payload.validated_data["reviewer"]
        if not can_review(reviewer, application.exporter):
            raise PermissionDenied("The selected user has no review grant for this exporter.")
        application.reviewer = reviewer
        application.save(update_fields=["reviewer", "updated_at"])
        services.audit(request, application.exporter, "reviewer_assigned", reviewer_id=str(reviewer.pk))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=None, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"], url_path="start-review")
    def start_review(self, request, pk=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, {"submitted"})
        application.status = "under_review"
        application.save(update_fields=["status", "updated_at"])
        services.audit(request, application.exporter, "review_started", application_id=str(application.pk))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=None, responses=s.ApplicationSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        application = self.get_object()
        assert_editor(request.user, application.exporter)
        services.assert_state(application, services.EDITABLE)
        services.require_complete(application)
        application.status, application.submitted_at = "submitted", timezone.now()
        application.save(update_fields=["status", "submitted_at", "updated_at"])
        services.audit(request, application.exporter, "application_submitted", application_id=str(application.pk))
        services.notify(application.exporter, "Export application submitted", "An export application is ready for review.")
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
        services.audit(request, application.exporter, "domain_reviewed", domain=key, verdict=section.status)
        return Response(s.DomainSerializer(section).data)

    @extend_schema(methods=["GET"], responses=s.DocumentSerializer(many=True))
    @extend_schema(methods=["POST"], request=s.DocumentUploadSerializer, responses={201: s.DocumentSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser], pagination_class=None)
    def documents(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(s.DocumentSerializer(application.documents.all(), many=True, context=self.context()).data)
        assert_editor(request.user, application.exporter)
        services.assert_state(application, services.EDITABLE | {"conditionally_approved", "approved"})
        payload = s.DocumentUploadSerializer(data=request.data, context=self.context(application=application))
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        previous = application.documents.filter(document_type=data["document_type"], is_current=True).first()
        if application.status == "approved" and not previous:
            raise ConflictError("Only renewal of existing credentials is allowed after approval.", code="renewal_required")
        if previous:
            for field in ["domain", "shipment", "condition"]:
                if data.get(field) != getattr(previous, field):
                    raise ConflictError("A document version must retain its domain and linked records.", code="document_identity_changed")
            previous.is_current = False
            previous.save(update_fields=["is_current"])
        document = payload.save(application=application, uploaded_by=request.user, original_name=Path(data["file"].name).name, version=previous.version + 1 if previous else 1)
        application.sections.filter(key=document.domain).update(status="pending", score=0, reviewed_at=None, reviewed_by=None)
        services.audit(request, application.exporter, "document_uploaded", document_id=str(document.pk), version=document.version)
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
        services.notify(application.exporter, "Information requested", item.reason)
        services.audit(request, application.exporter, "information_requested", request_id=str(item.pk))
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
        condition = m.ExportCondition.objects.create(application=application, **payload.validated_data)
        services.audit(request, application.exporter, "condition_created", condition_id=str(condition.pk))
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
            m.ExportCondition.objects.create(application=application, **condition)
        if conditional and not application.conditions.filter(cleared_at__isnull=True).exists():
            raise ConflictError("Conditional approval requires at least one outstanding condition.")
        application.status, application.rationale = data["status"], data["rationale"]
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "rationale", "reviewed_at", "updated_at"])
        services.audit(request, application.exporter, "decision_recorded", verdict=application.status)
        services.notify(application.exporter, "Export application decision", application.status)
        return Response(self.get_serializer(application).data)

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        application = self.get_object()
        return Response({"events": audit_rows(application.exporter_id)})


class DocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.ExportDocument.objects.none()
    serializer_class = s.DocumentSerializer
    filterset_fields = ["application", "domain", "status", "is_current", "shipment"]
    search_fields = ["title", "document_type", "issuer", "reference"]

    def get_queryset(self):
        return m.ExportDocument.objects.filter(application__exporter_id__in=exporter_ids(self.request.user))

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
        application = m.ExportApplication.objects.select_for_update().get(pk=document.application_id)
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
        services.audit(request, application.exporter, "document_reviewed", document_id=str(document.pk), verdict=document.status)
        return Response(self.get_serializer(document).data)


class RequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.InformationRequest.objects.none()
    serializer_class = s.InformationRequestSerializer
    filterset_fields = ["application", "status"]

    def get_queryset(self):
        return m.InformationRequest.objects.filter(application__exporter_id__in=exporter_ids(self.request.user))

    @extend_schema(request=s.RequestResponseSerializer, responses={201: s.RequestResponseSerializer})
    @action(detail=True, methods=["post"])
    def responses(self, request, pk=None):
        item = self.get_object()
        application = m.ExportApplication.objects.select_for_update().get(pk=item.application_id)
        assert_editor(request.user, application.exporter)
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
        services.audit(request, application.exporter, "request_responded", request_id=str(item.pk))
        return Response(s.RequestResponseSerializer(response).data, status=201)

    @extend_schema(request=s.ResponseReviewSerializer, responses=s.InformationRequestSerializer)
    @action(detail=True, methods=["post"], url_path="review-response")
    def review_response(self, request, pk=None):
        item = self.get_object()
        application = m.ExportApplication.objects.select_for_update().get(pk=item.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        if item.status != "responded":
            raise ConflictError("There is no unreviewed response.")
        payload = s.ResponseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item.status = "accepted" if payload.validated_data["accepted"] else "open"
        item.review_notes = payload.validated_data["notes"]
        item.save(update_fields=["status", "review_notes", "updated_at"])
        services.audit(request, application.exporter, "request_response_reviewed", request_id=str(item.pk), verdict=item.status)
        return Response(self.get_serializer(item).data)


class ConditionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.ExportCondition.objects.none()
    serializer_class = s.ConditionSerializer
    filterset_fields = ["application", "domain"]

    def get_queryset(self):
        return m.ExportCondition.objects.filter(application__exporter_id__in=exporter_ids(self.request.user))

    @extend_schema(request=s.ResolutionSerializer, responses=s.ConditionSerializer)
    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        condition = self.get_object()
        application = m.ExportApplication.objects.select_for_update().get(pk=condition.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.ResolutionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        documents = condition.documents.filter(is_current=True)
        if condition.cleared_at or not documents.exists() or documents.exclude(status="verified").exists() or documents.filter(expires_on__lt=timezone.localdate()).exists():
            raise ConflictError("An outstanding condition requires current verified evidence before clearance.")
        condition.cleared_at, condition.cleared_by = timezone.now(), request.user
        condition.save(update_fields=["cleared_at", "cleared_by", "updated_at"])
        services.audit(request, application.exporter, "condition_cleared", condition_id=str(condition.pk), notes=payload.validated_data["notes"])
        return Response(self.get_serializer(condition).data)


class NotificationViewSet(mixins.ListModelMixin, AtomicViewSet):
    queryset = m.Notification.objects.none()
    serializer_class = s.NotificationSerializer

    def get_queryset(self):
        return m.Notification.objects.filter(recipient=self.request.user, exporter_id__in=exporter_ids(self.request.user))

    @extend_schema(request=None, responses=s.NotificationSerializer)
    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)


def audit_rows(exporter_id):
    return list(AccountAuditEvent.objects.filter(event_type__startswith="export.", metadata__exporter_id=str(exporter_id)).order_by("-created_at").values("id", "event_type", "created_at", "actor_id")[:200])


class SummaryViewSet(AtomicViewSet):
    serializer_class = s.SummarySerializer

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def me(self, request):
        exporters = m.Exporter.objects.filter(pk__in=exporter_ids(request.user)).select_related("organisation")
        return Response({"is_staff": request.user.is_staff, "exporters": [
            {"id": exporter.pk, "name": exporter.organisation.name, "can_edit": can_edit(request.user, exporter), "can_review": can_review(request.user, exporter), "can_read": True}
            for exporter in exporters
        ]})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        exporters = m.Exporter.objects.filter(pk__in=exporter_ids(request.user)).select_related("organisation")
        data = []
        totals = {"products": 0, "buyers": 0, "shipments": 0, "open_requests": 0, "expiring_documents": 0}
        for exporter in exporters:
            application = m.ExportApplication.objects.filter(exporter=exporter).first()
            products = exporter.products.filter(is_active=True).count()
            buyers = exporter.buyers.filter(is_active=True).count()
            shipments = exporter.shipments.exclude(status__in=["cancelled"]).count()
            open_requests = application.requests.exclude(status="accepted").count() if application else 0
            expiring = application.documents.filter(is_current=True, expires_on__lte=timezone.localdate() + timedelta(days=30)).count() if application else 0
            totals["products"] += products
            totals["buyers"] += buyers
            totals["shipments"] += shipments
            totals["open_requests"] += open_requests
            totals["expiring_documents"] += expiring
            data.append({
                "exporter_id": exporter.pk,
                "reference": exporter.reference,
                "name": exporter.organisation.name,
                "application_id": application.pk if application else None,
                "status": application.status if application else "not_started",
                "progress": services.progress(application) if application else None,
                "risk": services.risk(application) if application else None,
                "products": products,
                "buyers": buyers,
                "shipments": shipments,
                "open_requests": open_requests,
                "expiring_documents": expiring,
            })
        return Response({"exporters": data, "exporter_count": len(data), "totals": totals, "unread_notifications": m.Notification.objects.filter(recipient=request.user, exporter__in=exporters, read_at__isnull=True).count()})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def risk(self, request):
        applications = m.ExportApplication.objects.filter(exporter_id__in=exporter_ids(request.user))
        rows = [{"exporter_id": app.exporter_id, "application_id": app.pk, **services.risk(app)} for app in applications]
        return Response({"applications": sorted(rows, key=lambda row: row["compliance_score"])})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=["get"])
    def audit(self, request):
        rows = []
        for pk in exporter_ids(request.user):
            rows.extend(audit_rows(pk))
        return Response({"events": sorted(rows, key=lambda row: row["created_at"], reverse=True)[:200]})


class ReportViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.ExportReport.objects.none()
    serializer_class = s.ReportSerializer

    def get_queryset(self):
        return m.ExportReport.objects.filter(requested_by=self.request.user)

    @extend_schema(request=None, responses={201: s.ReportSerializer})
    def create(self, request, *args, **kwargs):
        exporters = m.Exporter.objects.filter(pk__in=exporter_ids(request.user)).select_related("organisation")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Reference", "Exporter", "Application status", "Compliance score", "Risk band", "Products", "Buyers", "Shipments"])
        ids = []
        for exporter in exporters:
            ids.append(str(exporter.pk))
            application = m.ExportApplication.objects.filter(exporter=exporter).first()
            score = services.risk(application) if application else {}
            name = exporter.organisation.name
            if name.lstrip().startswith(("=", "+", "-", "@")):
                name = "'" + name
            writer.writerow([exporter.reference, name, application.status if application else "not_started", score.get("compliance_score", ""), score.get("risk_band", ""), exporter.products.filter(is_active=True).count(), exporter.buyers.filter(is_active=True).count(), exporter.shipments.exclude(status__in=["cancelled"]).count()])
            services.audit(request, exporter, "register_exported")
        report = m.ExportReport.objects.create(requested_by=request.user, exporter_ids=ids, content=output.getvalue())
        return Response(self.get_serializer(report).data, status=201)

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        report = self.get_object()
        if not set(report.exporter_ids).issubset({str(pk) for pk in exporter_ids(request.user)}):
            raise PermissionDenied("Your access to this report has changed; generate a new report.")
        response = HttpResponse(report.content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="export-register-{report.pk}.csv"'
        response["Cache-Control"] = "private, no-store"
        return response
