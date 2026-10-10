from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from common.exceptions import ConflictError, ResourceNotFoundError
from common.files import serve_stored_file
import json

from rest_framework.exceptions import PermissionDenied, ValidationError

from organisations.models import Organisation
from quality import bridge
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


def _notify_users(recipients, title, body, event, **links):
    users = {user.pk: user for user in recipients if user and getattr(user, "is_active", False)}
    m.QualityNotification.objects.bulk_create([
        m.QualityNotification(recipient=user, title=title, body=body, event=event, **links)
        for user in users.values()
    ])


def _organisation_users(org):
    if not org:
        return []
    from organisations.models import OrganisationMembership
    return [membership.user for membership in OrganisationMembership.objects.filter(organisation=org, is_active=True).select_related("user")]


def _operators():
    from accounts.models import User
    return User.objects.filter(is_staff=True, is_active=True)


class CertificateVerificationThrottle(SimpleRateThrottle):
    scope = "quality_certificate_verify"
    rate = "10/minute"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


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

    def get_queryset(self):
        qs = self.queryset
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            return qs
        role = services.derive_role(self.request.user)
        org = services.user_organisation(self.request.user)
        if role in {"operator", "regulator"}:
            return qs
        if role == "partner" and org:
            return qs.filter(organisation=org)
        return qs.none()

    def perform_create(self, serializer):
        role = services.require_role(self.request.user, "partner", "operator")
        org = services.user_organisation(self.request.user) if role == "partner" else None
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
        services.require_role(request.user, "operator", "regulator")
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
        app.save(update_fields=["documents", "audit", "updated_at"])
        bridge.carry_document_status_back(app, doc_id, doc["status"], request.user)
        return Response(s.PartnerDocumentSerializer(doc).data)

    @action(detail=True, methods=["post"], url_path=r"documents/(?P<doc_id>[^/.]+)/upload", parser_classes=[MultiPartParser, FormParser])
    def upload_document(self, request, pk=None, doc_id=None):
        app = self.get_object()
        services.require_role(request.user, "partner", "operator")
        doc = _find(app.documents, doc_id)
        if doc is None:
            raise ResourceNotFoundError("Document not found on this application.")
        serializer = s.QualityApplicationDocumentSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        uploaded, _ = m.QualityApplicationDocument.objects.update_or_create(
            application=app,
            document_id=doc_id,
            defaults={
                "name": serializer.validated_data.get("name") or doc.get("name", ""),
                "category": serializer.validated_data.get("category") or doc.get("category", ""),
                "file": serializer.validated_data["file"],
                "uploaded_by": request.user,
            },
        )
        doc["file_id"] = str(uploaded.id)
        doc["status"] = m.DocStatus.PENDING
        app.audit = [*app.audit, services.audit_entry(request.user, "document_uploaded", doc.get("name", "Document uploaded"))]
        app.save(update_fields=["documents", "audit", "updated_at"])
        return Response(s.QualityApplicationDocumentSerializer(uploaded, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path=r"documents/(?P<doc_id>[^/.]+)/download", url_name="document-download")
    def download_document(self, request, pk=None, doc_id=None):
        app = self.get_object()
        document = app.uploaded_documents.filter(document_id=doc_id).first()
        if (not document or not document.file) and app.compliance_application_id and _find(app.documents, doc_id):
            # A mirrored onboarding application keeps its files on the
            # application itself; the desk reads them from there.
            document = app.compliance_application.documents.filter(pk=doc_id).first()
        if not document or not document.file:
            raise ResourceNotFoundError("No file has been uploaded for this document.")
        return serve_stored_file(document.file, document.original_name or document.name or "quality-document")

    @action(detail=True, methods=["post"], url_path=r"risk-flags/(?P<flag_id>[^/.]+)/resolve")
    def resolve_risk_flag(self, request, pk=None, flag_id=None):
        services.require_role(request.user, "operator", "regulator")
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
        bridge.carry_decision_back(app, request.user, note)
        _notify_users(_organisation_users(app.organisation), "Quality application decision", f"{app.reference} was {app.status}.", "application_decision", application=app)
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


class QualityProfessionalApplicationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet
):
    """Individuals applying to work under a Q&C organisation.

    Anyone signed in may apply for themselves and read their own application;
    Beldium's quality operators read and decide all of them.
    """

    serializer_class = s.QualityProfessionalApplicationSerializer
    queryset = m.QualityProfessionalApplication.objects.all().select_related("applicant", "organisation").prefetch_related("documents")
    filterset_fields = ["status"]
    search_fields = ["reference", "applicant__email", "organisation__name"]
    http_method_names = ["get", "post", "head", "options"]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        if self.request.user.is_staff:
            return self.queryset
        return self.queryset.filter(applicant=self.request.user)

    def create(self, request, *args, **kwargs):
        # One multipart request: the answers as a JSON string under `data`,
        # each file under its document type.
        try:
            payload = json.loads(request.data.get("data") or "")
        except (TypeError, ValueError):
            raise ValidationError({"data": ["Send the application as a JSON string."]})
        serializer = s.ProfessionalApplicationInputSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        organisation = Organisation.objects.filter(pk=data["organisation"]).first()
        if organisation is None:
            raise ValidationError({"organisation": ["Select a registered organisation."]})

        files = {}
        for document_type, title, required in s.PROFESSIONAL_DOCUMENTS:
            upload = request.FILES.get(document_type)
            if upload is None:
                if required:
                    raise ValidationError({document_type: [f"{title} is required."]})
                continue
            try:
                files[document_type] = (title, s.validate_upload(upload))
            except ValidationError as error:
                raise ValidationError({document_type: error.detail})

        open_statuses = [m.ApplicationStatus.SUBMITTED, m.ApplicationStatus.IN_REVIEW, m.ApplicationStatus.APPROVED]
        if self.queryset.filter(applicant=request.user, status__in=open_statuses).exists():
            raise ConflictError("You already have a professional application with Beldium.")

        application = m.QualityProfessionalApplication.objects.create(
            applicant=request.user, organisation=organisation, role=data["role"],
            personal=data["personal"], qualifications=data["qualifications"],
            certifications=data["certifications"], capability=data["capability"],
            experience=data["experience"], declaration=data["declaration"],
            audit=[services.audit_entry(request.user, "submitted", "Application submitted")],
        )
        for document_type, (title, upload) in files.items():
            m.QualityProfessionalDocument.objects.create(
                application=application, document_type=document_type, title=title, file=upload,
            )
        _notify_users(_operators(), "New professional application", f"{application.reference} was submitted.", "professional_application_submitted")
        return Response(self.get_serializer(application).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def decide(self, request, pk=None):
        application = self.get_object()
        if not services.can_decide(request.user):
            raise PermissionDenied("Only Beldium quality operators may decide applications.")
        if application.status in {m.ApplicationStatus.APPROVED, m.ApplicationStatus.REJECTED}:
            raise ConflictError("This application has already reached a final decision.")
        serializer = s.DecideApplicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application.status = serializer.validated_data["status"]
        note = serializer.validated_data.get("note", "")
        if note:
            application.decision_note = note
        application.audit = [*application.audit, services.audit_entry(
            request.user, "decision", f"Status set to {application.status}. {note}".strip(),
        )]
        application.save(update_fields=["status", "decision_note", "audit", "updated_at"])
        _notify_users([application.applicant], "Professional application decision", f"{application.reference} was {application.status}.", "professional_application_decision")
        return Response(self.get_serializer(application).data)

    @action(detail=True, methods=["get"], url_path=r"documents/(?P<doc_id>[^/.]+)/download", url_name="document-download")
    def download_document(self, request, pk=None, doc_id=None):
        document = self.get_object().documents.filter(pk=doc_id).first()
        if not document or not document.file:
            raise ResourceNotFoundError("Document not found on this application.")
        return serve_stored_file(document.file, document.original_name)


class SampleViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, AtomicViewSet
):
    queryset = m.Sample.objects.all().select_related("buyer_spec", "miner_organisation", "partner_organisation", "buyer_organisation", "registered_by")
    filterset_fields = ["status", "buyer_spec"]
    search_fields = ["reference", "material", "lot", "mine_site"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_serializer_class(self):
        if self.action == "create":
            return s.NewSampleSerializer
        if self.action in {"update", "partial_update"}:
            return s.SampleStatusSerializer
        return s.SampleSerializer

    def get_queryset(self):
        qs = self.queryset
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            return qs
        role = services.derive_role(self.request.user)
        org = services.user_organisation(self.request.user)
        if role in {"operator", "regulator"}:
            return qs
        if role == "miner" and org:
            return qs.filter(Q(miner_organisation=org) | Q(registered_by__organisation_memberships__organisation=org, registered_by__organisation_memberships__is_active=True)).distinct()
        if role == "partner" and org:
            return qs.filter(partner_organisation=org)
        if role == "buyer" and org:
            return qs.filter(Q(buyer_organisation=org) | Q(buyer_spec__buyer_organisation=org))
        return qs.none()

    def create(self, request, *args, **kwargs):
        services.require_role(request.user, "miner", "operator")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        miner_org = services.user_organisation(request.user)
        buyer_org = data.get("buyer_spec").buyer_organisation if data.get("buyer_spec") else None
        sample = m.Sample.objects.create(
            material=data["material"],
            lot=data.get("lot", ""),
            mine_site=data.get("mine_site", ""),
            origin=data.get("origin", ""),
            mass_kg=data.get("mass_kg", 0),
            buyer_spec=data.get("buyer_spec"),
            miner_organisation=miner_org,
            miner_org=getattr(miner_org, "name", ""),
            buyer_organisation=buyer_org,
            buyer_org=getattr(buyer_org, "name", ""),
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
            services.require_role(request.user, "operator", "partner")
            services.transition_sample(sample, new_status)
            sample.audit = [*sample.audit, services.audit_entry(request.user, "status_changed", f"Status set to {new_status}")]
            sample.save(update_fields=["status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def custody(self, request, pk=None):
        services.require_role(request.user, "miner", "partner", "operator")
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
            services.transition_sample(sample, m.SampleStatus.IN_TRANSIT)
        elif sample.status == m.SampleStatus.IN_TRANSIT and services.derive_role(request.user) in {"partner", "operator"}:
            services.transition_sample(sample, m.SampleStatus.RECEIVED)
        sample.audit = [*sample.audit, services.audit_entry(request.user, "custody", data["action"])]
        sample.save(update_fields=["custody", "status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"], url_path="test-request")
    def test_request(self, request, pk=None):
        services.require_role(request.user, "partner", "operator")
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
        sample.results = services.result_rows_from_spec(sample.buyer_spec, data["methods"])
        if sample.status == m.SampleStatus.IN_TRANSIT:
            services.transition_sample(sample, m.SampleStatus.RECEIVED)
        services.transition_sample(sample, m.SampleStatus.TESTING)
        sample.audit = [*sample.audit, services.audit_entry(request.user, "test_requested", ", ".join(data["methods"]))]
        if services.derive_role(request.user) == "partner":
            org = services.user_organisation(request.user)
            sample.partner_organisation = org
            sample.partner_org = getattr(org, "name", "")
            update_fields = ["test_request", "results", "status", "audit", "partner_organisation", "partner_org", "updated_at"]
        else:
            update_fields = ["test_request", "results", "status", "audit", "updated_at"]
        sample.save(update_fields=update_fields)
        _notify_users(_organisation_users(sample.partner_organisation), "Sample assigned", f"{sample.reference} is assigned for testing.", "sample_assigned", sample=sample)
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["patch"], url_path=r"results/(?P<result_id>[^/.]+)")
    def result(self, request, pk=None, result_id=None):
        services.require_role(request.user, "partner", "operator")
        sample = self.get_object()
        serializer = s.ResultVerdictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = _find(sample.results, result_id)
        if item is None:
            raise ResourceNotFoundError("Result not found on this sample.")
        for field in ("unit", "spec", "uncertainty"):
            if field in serializer.validated_data:
                item[field] = serializer.validated_data[field]
        if "value" in serializer.validated_data:
            item["value"] = serializer.validated_data["value"]
        item["verdict"] = services.limit_verdict(item.get("limit", {}), item.get("value"))
        sample.audit = [*sample.audit, services.audit_entry(
            request.user, "result_updated", f"{item.get('analyte', 'Result')} -> {item['verdict']}",
        )]
        sample.save(update_fields=["results", "audit", "updated_at"])
        _notify_users([sample.registered_by, *_operators()], "Test result updated", f"{item.get('analyte', 'Result')} is {item['verdict']}.", "test_verdict", sample=sample)
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
        services.transition_sample(sample, m.SampleStatus.REVIEWED if data["verdict"] != m.ResultVerdict.FAIL else m.SampleStatus.REJECTED)
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
            valid_until=timezone.now() + timezone.timedelta(days=365),
            verification_hash=services.gen_hash(sample.reference, "certificate"),
        )
        services.transition_sample(sample, m.SampleStatus.CERTIFIED)
        sample.audit = [*sample.audit, services.audit_entry(request.user, "certified", cert.reference)]
        sample.save(update_fields=["status", "audit", "updated_at"])
        _notify_users([sample.registered_by, *_organisation_users(sample.buyer_organisation)], "Certificate issued", f"{cert.reference} was issued for {sample.reference}.", "certificate_issued", sample=sample, certificate=cert)
        return Response(s.CertificateSerializer(cert).data, status=status.HTTP_201_CREATED)


class CertificateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.Certificate.objects.all().select_related("sample")
    filterset_fields = ["status", "sample"]
    search_fields = ["reference", "sample__reference"]
    http_method_names = ["get", "post", "head", "options"]
    serializer_class = s.CertificateSerializer

    def get_queryset(self):
        qs = self.queryset
        role = services.derive_role(self.request.user)
        org = services.user_organisation(self.request.user)
        if role in {"operator", "regulator"}:
            return qs
        if role == "miner" and org:
            return qs.filter(sample__miner_organisation=org)
        if role == "partner" and org:
            return qs.filter(sample__partner_organisation=org)
        if role == "buyer" and org:
            return qs.filter(Q(sample__buyer_organisation=org) | Q(sample__buyer_spec__buyer_organisation=org))
        return qs.none()

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
        _notify_users([cert.sample.registered_by, *_organisation_users(cert.sample.buyer_organisation)], "Certificate revoked", f"{cert.reference} was revoked.", "certificate_revoked", sample=cert.sample, certificate=cert)
        return Response(s.CertificateSerializer(cert).data)


class BuyerSpecViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    queryset = m.BuyerSpec.objects.all()
    serializer_class = s.BuyerSpecSerializer
    search_fields = ["name", "buyer_org", "material"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        qs = self.queryset
        role = services.derive_role(self.request.user)
        org = services.user_organisation(self.request.user)
        if role in {"operator", "regulator"}:
            return qs
        if role == "buyer" and org:
            return qs.filter(buyer_organisation=org)
        return qs

    def perform_create(self, serializer):
        role = services.require_role(self.request.user, "buyer", "operator")
        org = serializer.validated_data.get("buyer_organisation")
        if role == "buyer":
            org = services.user_organisation(self.request.user)
        serializer.save(buyer_organisation=org, buyer_org=getattr(org, "name", serializer.validated_data.get("buyer_org", "")))

    def perform_update(self, serializer):
        services.require_role(self.request.user, "buyer", "operator")
        serializer.save()


class NonConformityViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet
):
    queryset = m.QualityNonConformity.objects.all()
    serializer_class = s.QualityNonConformitySerializer
    filterset_fields = ["status", "severity"]
    search_fields = ["reference", "title", "against"]
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        services.require_role(request.user, "operator", "regulator", "partner")
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
        _notify_users(_operators(), "Non-conformity raised", nc.title, "non_conformity_raised", non_conformity=nc)
        return Response(s.QualityNonConformitySerializer(nc).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def capa(self, request, pk=None):
        services.require_role(request.user, "operator", "partner")
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
        services.require_role(request.user, "operator", "partner")
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
        services.require_role(request.user, "operator", "regulator")
        nc = self.get_object()
        if nc.status == m.NCStatus.CLOSED:
            raise ConflictError("Non-conformity is already closed.")
        if nc.capa and any(item["status"] != m.CAPAStatus.COMPLETE for item in nc.capa):
            raise ConflictError("All corrective actions must be complete before closing.")
        nc.status = m.NCStatus.CLOSED
        nc.save(update_fields=["status", "updated_at"])
        _notify_users(_operators(), "Non-conformity closed", nc.title, "non_conformity_closed", non_conformity=nc)
        return Response(s.QualityNonConformitySerializer(nc).data)


class CertificateVerificationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CertificateVerificationThrottle]

    def get(self, request, verification_hash):
        cert = m.Certificate.objects.select_related("sample").filter(verification_hash=verification_hash).first()
        if not cert:
            raise ResourceNotFoundError("Certificate not found.")
        m.Certificate.objects.filter(pk=cert.pk).update(scans=F("scans") + 1)
        cert.refresh_from_db()
        return Response(s.CertificateVerificationSerializer(cert).data)


class QualityNotificationViewSet(mixins.ListModelMixin, AtomicViewSet):
    serializer_class = s.QualityNotificationSerializer
    queryset = m.QualityNotification.objects.none()

    def get_queryset(self):
        return m.QualityNotification.objects.filter(recipient=self.request.user)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return Response(s.QualityNotificationSerializer(notification).data)
