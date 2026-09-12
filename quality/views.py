from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import ConflictError, ResourceNotFoundError
from quality import models as m
from quality import serializers as s
from quality import services


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


def _find(items, item_id):
    for item in items:
        if str(item.get("id")) == str(item_id):
            return item
    return None


class SummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=s.QualityCapabilitiesSerializer)
    def get(self, request, kind):
        user = request.user
        role = services.derive_role(user)
        capabilities = {
            "role": role,
            "can_review": services.can_review(user),
            "can_decide": services.can_decide(user),
            "is_staff": bool(user.is_staff),
        }
        if kind == "me":
            return Response(s.QualityCapabilitiesSerializer(capabilities).data)

        totals = {
            "applications": m.QualityApplication.objects.count(),
            "applications_in_review": m.QualityApplication.objects.filter(status=m.ApplicationStatus.IN_REVIEW).count(),
            "partners_approved": m.QualityApplication.objects.filter(status=m.ApplicationStatus.APPROVED).count(),
            "samples": m.Sample.objects.count(),
            "samples_in_testing": m.Sample.objects.filter(status=m.SampleStatus.TESTING).count(),
            "certificates_active": m.Certificate.objects.filter(status=m.CertificateStatus.ACTIVE).count(),
            "open_non_conformities": m.QualityNonConformity.objects.exclude(status=m.NCStatus.CLOSED).count(),
        }
        recent = m.QualityApplication.objects.all()[:10]
        data = {
            "role": role,
            "capabilities": capabilities,
            "totals": totals,
            "recent_applications": s.QualityApplicationSerializer(recent, many=True).data,
        }
        return Response(data)


class QualityApplicationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet
):
    queryset = m.QualityApplication.objects.all().select_related("assigned_to", "organisation")
    filterset_fields = ["status"]
    search_fields = ["reference", "organisation__name"]
    http_method_names = ["get", "post", "head", "options", "patch"]

    def get_serializer_class(self):
        if self.action == "create":
            return s.QualityApplicationCreateSerializer
        return s.QualityApplicationSerializer

    def perform_create(self, serializer):
        org = None
        request_org_id = self.request.data.get("organisation_id")
        if request_org_id:
            from organisations.models import Organisation
            org = Organisation.objects.filter(pk=request_org_id).first()
        app = serializer.save(organisation=org)
        app.audit = [services.audit_entry(self.request.user, "submitted", "Application submitted")]
        app.save(update_fields=["audit"])

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        instance = serializer.instance
        return Response(s.QualityApplicationSerializer(instance).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path=r"documents/(?P<doc_id>[^/.]+)")
    def document_status(self, request, pk=None, doc_id=None):
        app = self.get_object()
        serializer = s.SetDocumentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        doc = _find(app.documents, doc_id)
        if doc is None:
            raise ResourceNotFoundError("Document not found on this application.")
        doc["status"] = serializer.validated_data["status"]
        app.audit = [*app.audit, services.audit_entry(
            request.user, "document_status", f"{doc.get('name', 'Document')} marked {doc['status']}",
        )]
        app.save(update_fields=["documents", "audit"])
        return Response(s.PartnerDocumentSerializer(doc).data)

    @action(detail=True, methods=["post"], url_path=r"risk-flags/(?P<flag_id>[^/.]+)/resolve")
    def resolve_risk_flag(self, request, pk=None, flag_id=None):
        app = self.get_object()
        flag = _find(app.risk_flags, flag_id)
        if flag is None:
            raise ResourceNotFoundError("Risk flag not found on this application.")
        flag["resolved"] = True
        app.audit = [*app.audit, services.audit_entry(
            request.user, "risk_flag_resolved", flag.get("title", "Risk flag resolved"),
        )]
        app.save(update_fields=["risk_flags", "audit"])
        return Response(s.RiskFlagSerializer(flag).data)

    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        app = self.get_object()
        if not services.can_decide(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only Beldium quality operators may decide applications.")
        if app.status in {m.ApplicationStatus.APPROVED, m.ApplicationStatus.REJECTED}:
            raise ConflictError("This application has already reached a final decision.")
        serializer = s.DecideApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        app.status = serializer.validated_data["status"]
        note = serializer.validated_data.get("note", "")
        if note:
            app.decision_note = note
        app.audit = [*app.audit, services.audit_entry(request.user, "decision", f"Status set to {app.status}. {note}".strip())]
        app.save(update_fields=["status", "decision_note", "audit", "updated_at"])
        return Response(s.QualityApplicationSerializer(app).data)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        app = self.get_object()
        if not services.can_review(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have quality review authority.")
        app.assigned_to = request.user
        if app.status == m.ApplicationStatus.SUBMITTED:
            app.status = m.ApplicationStatus.IN_REVIEW
        app.audit = [*app.audit, services.audit_entry(request.user, "assigned", "Application assigned for review")]
        app.save(update_fields=["assigned_to", "status", "audit", "updated_at"])
        return Response(s.QualityApplicationSerializer(app).data)


class SampleViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, AtomicViewSet
):
    queryset = m.Sample.objects.all().select_related("buyer_spec")
    filterset_fields = ["status", "buyer_spec"]
    search_fields = ["reference", "material", "lot", "mine_site"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return s.NewSampleSerializer
        if self.action in {"update", "partial_update"}:
            return s.SampleStatusSerializer
        return s.SampleSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sample = m.Sample.objects.create(
            material=data["material"],
            lot=data.get("lot", ""),
            mine_site=data.get("mine_site", ""),
            origin=data.get("origin", ""),
            mass_kg=data.get("mass_kg", 0),
            buyer_spec=data.get("buyer_spec"),
            registered_by=request.user,
            audit=[services.audit_entry(request.user, "registered", "Sample registered")],
        )
        return Response(s.SampleSerializer(sample).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        sample = self.get_object()
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data.get("status")
        if new_status:
            sample.status = new_status
            sample.audit = [*sample.audit, services.audit_entry(request.user, "status_changed", f"Status set to {new_status}")]
            sample.save(update_fields=["status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def custody(self, request, pk=None):
        sample = self.get_object()
        serializer = s.CustodyEventInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        event = {
            "id": services.new_id(),
            "at": timezone.now().isoformat(),
            "actor": services.actor_label(request.user),
            "location": data.get("location", ""),
            "action": data["action"],
            "seal_intact": data.get("seal_intact", True),
            "hash": services.gen_hash(sample.reference, data["action"]),
        }
        sample.custody = [*sample.custody, event]
        if sample.status == m.SampleStatus.REGISTERED:
            sample.status = m.SampleStatus.IN_TRANSIT
        sample.audit = [*sample.audit, services.audit_entry(request.user, "custody", data["action"])]
        sample.save(update_fields=["custody", "status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"], url_path="test-request")
    def test_request(self, request, pk=None):
        sample = self.get_object()
        serializer = s.TestRequestInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sample.test_request = {
            "id": services.new_id(),
            "requested_at": timezone.now().isoformat(),
            "priority": data["priority"],
            "methods": data["methods"],
            "turnaround": data.get("turnaround", ""),
            "status": "submitted",
        }
        sample.results = [
            {
                "id": services.new_id(), "analyte": method, "method": method, "value": "",
                "unit": "", "spec": "", "verdict": m.ResultVerdict.PENDING, "uncertainty": "",
            }
            for method in data["methods"]
        ]
        sample.status = m.SampleStatus.TESTING
        sample.audit = [*sample.audit, services.audit_entry(request.user, "test_requested", ", ".join(data["methods"]))]
        sample.save(update_fields=["test_request", "results", "status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["patch"], url_path=r"results/(?P<result_id>[^/.]+)")
    def result(self, request, pk=None, result_id=None):
        sample = self.get_object()
        serializer = s.ResultVerdictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = _find(sample.results, result_id)
        if item is None:
            raise ResourceNotFoundError("Result not found on this sample.")
        item["verdict"] = serializer.validated_data["verdict"]
        if "value" in serializer.validated_data:
            item["value"] = serializer.validated_data["value"]
        sample.audit = [*sample.audit, services.audit_entry(
            request.user, "result_updated", f"{item.get('analyte', 'Result')} -> {item['verdict']}",
        )]
        sample.save(update_fields=["results", "audit", "updated_at"])
        return Response(s.TestResultSerializer(item).data)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        sample = self.get_object()
        if not services.can_review(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have quality review authority.")
        serializer = s.QualityReviewInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sample.quality_review = {
            "reviewer": services.actor_label(request.user),
            "at": timezone.now().isoformat(),
            "verdict": data["verdict"],
            "note": data.get("note", ""),
        }
        sample.status = m.SampleStatus.REVIEWED if data["verdict"] != m.ResultVerdict.FAIL else m.SampleStatus.REJECTED
        sample.audit = [*sample.audit, services.audit_entry(request.user, "reviewed", f"Verdict: {data['verdict']}")]
        sample.save(update_fields=["quality_review", "status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def certificate(self, request, pk=None):
        sample = self.get_object()
        if not services.can_decide(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only Beldium quality operators may issue certificates.")
        if sample.status != m.SampleStatus.REVIEWED:
            raise ConflictError("Only reviewed samples can be certified.")
        cert = m.Certificate.objects.create(
            sample=sample,
            issued_by=services.actor_label(request.user),
            verification_hash=services.gen_hash(sample.reference, "certificate"),
        )
        sample.status = m.SampleStatus.CERTIFIED
        sample.audit = [*sample.audit, services.audit_entry(request.user, "certified", cert.reference)]
        sample.save(update_fields=["status", "audit", "updated_at"])
        return Response(s.CertificateSerializer(cert).data, status=status.HTTP_201_CREATED)


class CertificateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.Certificate.objects.all().select_related("sample")
    filterset_fields = ["status", "sample"]
    search_fields = ["reference", "sample__reference"]
    http_method_names = ["get", "post", "head", "options"]
    serializer_class = s.CertificateSerializer

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        cert = self.get_object()
        if not services.can_decide(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only Beldium quality operators may revoke certificates.")
        if cert.status == m.CertificateStatus.REVOKED:
            raise ConflictError("Certificate is already revoked.")
        cert.status = m.CertificateStatus.REVOKED
        cert.save(update_fields=["status", "updated_at"])
        return Response(s.CertificateSerializer(cert).data)


class BuyerSpecViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.BuyerSpec.objects.all()
    serializer_class = s.BuyerSpecSerializer
    search_fields = ["name", "buyer_org", "material"]
    http_method_names = ["get", "head", "options"]


class NonConformityViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet
):
    queryset = m.QualityNonConformity.objects.all()
    serializer_class = s.QualityNonConformitySerializer
    filterset_fields = ["status", "severity"]
    search_fields = ["reference", "title", "against"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        serializer = s.RaiseNonConformitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        nc = m.QualityNonConformity.objects.create(
            title=data["title"],
            against=data.get("against", ""),
            severity=data["severity"],
            detail=data.get("detail", ""),
            raised_by=services.actor_label(request.user),
            raised_by_user=request.user,
        )
        return Response(s.QualityNonConformitySerializer(nc).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def capa(self, request, pk=None):
        nc = self.get_object()
        if nc.status == m.NCStatus.CLOSED:
            raise ConflictError("Cannot add a corrective action to a closed non-conformity.")
        serializer = s.AddCorrectiveActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item = {
            "id": services.new_id(),
            "action": data["action"],
            "owner": data.get("owner", ""),
            "due": data.get("due", ""),
            "status": m.CAPAStatus.OPEN,
        }
        nc.capa = [*nc.capa, item]
        nc.status = m.NCStatus.CAPA_SUBMITTED
        nc.save(update_fields=["capa", "status", "updated_at"])
        return Response(s.CorrectiveActionSerializer(item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path=r"capa/(?P<action_id>[^/.]+)/advance")
    def advance_capa(self, request, pk=None, action_id=None):
        nc = self.get_object()
        item = _find(nc.capa, action_id)
        if item is None:
            raise ResourceNotFoundError("Corrective action not found on this non-conformity.")
        order = [m.CAPAStatus.OPEN, m.CAPAStatus.IN_PROGRESS, m.CAPAStatus.COMPLETE]
        idx = order.index(item["status"]) if item["status"] in order else 0
        if idx >= len(order) - 1:
            raise ConflictError("Corrective action is already complete.")
        item["status"] = order[idx + 1]
        nc.save(update_fields=["capa", "updated_at"])
        return Response(s.CorrectiveActionSerializer(item).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        nc = self.get_object()
        if nc.status == m.NCStatus.CLOSED:
            raise ConflictError("Non-conformity is already closed.")
        if nc.capa and any(item["status"] != m.CAPAStatus.COMPLETE for item in nc.capa):
            raise ConflictError("All corrective actions must be complete before closing.")
        nc.status = m.NCStatus.CLOSED
        nc.save(update_fields=["status", "updated_at"])
        return Response(s.QualityNonConformitySerializer(nc).data)
