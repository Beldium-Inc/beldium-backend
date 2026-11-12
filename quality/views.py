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

from organisations.rights import build_me
from organisations.models import Organisation
from accounts import second_factor
from quality import audit_log
from quality import bridge
from quality import permissions
from quality import roles as role_data
from quality import rules
from quality import models as m
from quality import serializers as s
from quality import services


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class AtomicViewSet(viewsets.GenericViewSet):
    """Every call runs in one transaction. A successful write is written to
    the audit log inside that transaction. A refused call (403, or a 404 that
    hides a record the caller may not see) is rolled back and then written to
    the audit log on its own, so the refusal is kept."""

    permission_classes = [IsAuthenticated]
    audit_record_type = ""
    audit_writes = True

    def dispatch(self, request, *args, **kwargs):
        self.audit_detail = {}
        with transaction.atomic():
            result = super().dispatch(request, *args, **kwargs)
            if result.status_code >= 400:
                transaction.set_rollback(True)
            elif self.audit_writes and request.method not in SAFE_METHODS:
                self._write_audit(result, kwargs, m.AuditOutcome.SUCCESS)
        if result.status_code in {403, 404}:
            user = getattr(self.request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                with transaction.atomic():
                    self._write_audit(result, kwargs, m.AuditOutcome.REFUSED)
        return result

    def _write_audit(self, result, kwargs, outcome):
        data = getattr(result, "data", None)
        record_id = kwargs.get("pk") or ""
        label = ""
        reason = ""
        if isinstance(data, dict):
            if not record_id and outcome == m.AuditOutcome.SUCCESS:
                record_id = data.get("id") or ""
            label = data.get("reference") or ""
            if outcome == m.AuditOutcome.REFUSED:
                reason = data.get("message") or str(data.get("detail") or "")
        detail = getattr(self, "audit_detail", {}) or {}
        audit_log.record(
            actor=self.request.user,
            action=f"{self.audit_record_type or self.basename}.{getattr(self, 'action', None) or self.request.method.lower()}",
            record_type=self.audit_record_type,
            record_id=record_id,
            record_label=detail.get("record_label", label),
            outcome=outcome,
            reason=detail.get("reason", reason) if outcome == m.AuditOutcome.SUCCESS else reason,
            before=detail.get("before") if outcome == m.AuditOutcome.SUCCESS else None,
            after=detail.get("after") if outcome == m.AuditOutcome.SUCCESS else None,
            request=self.request,
        )

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
            rights, restricted = permissions.permissions_for(user)
            roles = [
                {
                    "role_code": assignment.role_id,
                    "organisation_id": str(assignment.organisation_id) if assignment.organisation_id else None,
                    "organisation_name": getattr(assignment.organisation, "name", ""),
                    "valid_from": assignment.valid_from,
                    "valid_to": assignment.valid_to,
                }
                for assignment in permissions.active_assignments(user)
            ]
            return Response({
                **build_me(user, "quality"),
                **s.QualityCapabilitiesSerializer(capabilities).data,
                "user_id": str(user.id),
                "roles": roles,
                "role_codes": sorted(permissions.role_codes(user)),
                "permissions": rights,
                "restricted_views": restricted,
                "second_factor_enrolled": second_factor.is_enrolled(user),
            })

        # Platform wide totals and other organisations' applications are for
        # the quality desk and oversight only.
        services.require_role(user, "operator", "regulator")

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
    audit_record_type = "quality_application"
    queryset = m.QualityApplication.objects.all().select_related("assigned_to", "organisation")
    filterset_fields = ["status"]
    search_fields = ["reference", "organisation__name"]
    http_method_names = ["get", "post", "head", "options", "patch"]

    def get_serializer_class(self):
        if self.action == "create":
            return s.QualityApplicationCreateSerializer
        return s.QualityApplicationSerializer

    def get_queryset(self):
        # The same scope applies to reads and writes, so a record the caller
        # cannot read is a record the caller cannot change.
        qs = self.queryset
        role = services.derive_role(self.request.user)
        if role == "operator":
            return qs
        if role == "regulator" and self.request.method in {"GET", "HEAD", "OPTIONS"}:
            return qs
        org = services.applicant_organisation(self.request.user)
        if org:
            return qs.filter(organisation=org)
        return qs.none()

    def perform_create(self, serializer):
        if services.derive_role(self.request.user) == "operator":
            # Only the quality desk may file on behalf of a named organisation.
            org = None
            request_org_id = self.request.data.get("organisation_id")
            if request_org_id:
                org = Organisation.objects.filter(pk=request_org_id).first()
                if org is None:
                    raise ValidationError({"organisation_id": ["Select a registered organisation."]})
        else:
            # Anyone else files for their own organisation. An organisation id
            # sent by the client is ignored.
            org = services.applicant_organisation(self.request.user)
            if org is None:
                raise PermissionDenied("You do not have permission to perform this action.")
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
        services.require_role(request.user, "operator")
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
        # get_object is scoped: only the applicant's own members or the
        # quality desk reach this point.
        app = self.get_object()
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
        services.require_role(request.user, "operator")
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
        if not services.can_decide(request.user):
            raise PermissionDenied("Only Beldium quality operators may decide applications.")
        app = self.get_object()
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
        if not services.can_review(request.user):
            raise PermissionDenied("You do not have quality review authority.")
        app = self.get_object()
        if app.status in {m.ApplicationStatus.APPROVED, m.ApplicationStatus.REJECTED}:
            raise ConflictError("This application has already reached a final decision.")
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

    audit_record_type = "professional_application"
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
        for document_type, title, required in s.DOCUMENTS_BY_PROFESSIONAL_ROLE[data["role"]]:
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


PARTY_NAME_FIELDS = ("miner_org", "buyer_org", "miner_organisation", "buyer_organisation")


def _hide_party_names(user):
    """Decision 10: the laboratory does not see supplier or buyer names,
    behind a setting that defaults to hidden."""
    if services.derive_role(user) != "partner":
        return False
    try:
        return not rules.get("laboratory_sees_party_names")
    except rules.RuleNotSet:
        return True


def _strip_party_names(row):
    if isinstance(row, dict):
        for field in PARTY_NAME_FIELDS:
            if field in row:
                row[field] = None if field.endswith("organisation") else ""


class SampleViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
    mixins.UpdateModelMixin, AtomicViewSet
):
    audit_record_type = "sample"
    queryset = m.Sample.objects.all().select_related("buyer_spec", "miner_organisation", "partner_organisation", "buyer_organisation", "registered_by")
    filterset_fields = ["status", "buyer_spec"]
    search_fields = ["reference", "material", "lot", "mine_site"]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        data = getattr(response, "data", None)
        if data is not None and response.status_code < 400 and _hide_party_names(request.user):
            # A paged list has count and results. A single sample also has a
            # results field (its test results), so count tells them apart.
            paged = isinstance(data, dict) and "count" in data and "results" in data
            rows = data["results"] if paged else data
            for row in rows if isinstance(rows, list) else [rows]:
                _strip_party_names(row)
        return response

    def get_serializer_class(self):
        if self.action == "create":
            return s.NewSampleSerializer
        if self.action in {"update", "partial_update"}:
            return s.SampleStatusSerializer
        return s.SampleSerializer

    def get_queryset(self):
        # The same scope applies to reads and writes, so a sample the caller
        # cannot read is a sample the caller cannot change. A regulator reads
        # every sample and writes none.
        qs = self.queryset
        role = services.derive_role(self.request.user)
        org = services.user_organisation(self.request.user)
        if role == "operator":
            return qs
        if role == "regulator":
            return qs if self.request.method in {"GET", "HEAD", "OPTIONS"} else qs.none()
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
            # Moving a sample by hand, including rejecting it, is a quality
            # desk decision. Laboratories move samples through custody, test
            # requests and results.
            services.require_role(request.user, "operator")
            services.transition_sample(sample, new_status)
            sample.audit = [*sample.audit, services.audit_entry(request.user, "status_changed", f"Status set to {new_status}")]
            sample.save(update_fields=["status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def custody(self, request, pk=None):
        services.require_role(request.user, "miner", "partner", "operator")
        sample = self.get_object()
        if sample.status in {m.SampleStatus.CERTIFIED, m.SampleStatus.REJECTED}:
            raise ConflictError("Custody cannot be added to a sample that is certified or rejected.")
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
        role = services.require_role(request.user, "partner", "operator")
        # A laboratory reaches only samples already assigned to it, so it can
        # no longer take over someone else's sample by requesting a test.
        sample = self.get_object()
        if sample.test_request:
            raise ConflictError("A test has already been requested for this sample.")
        serializer = s.TestRequestInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        laboratory = data.get("laboratory")
        if laboratory is not None:
            if role != "operator":
                raise PermissionDenied("Only Beldium quality operators assign a laboratory.")
            sample.partner_organisation = laboratory
            sample.partner_org = laboratory.name
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
        sample.save(update_fields=["test_request", "results", "status", "audit", "partner_organisation", "partner_org", "updated_at"])
        _notify_users(_organisation_users(sample.partner_organisation), "Sample assigned", f"{sample.reference} is assigned for testing.", "sample_assigned", sample=sample)
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["patch"], url_path=r"results/(?P<result_id>[^/.]+)")
    def result(self, request, pk=None, result_id=None):
        services.require_role(request.user, "partner", "operator")
        sample = self.get_object()
        if sample.status != m.SampleStatus.TESTING:
            raise ConflictError("Results can only be changed while the sample is in testing.")
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
        if not services.can_review(request.user):
            raise PermissionDenied("You do not have quality review authority.")
        sample = self.get_object()
        if sample.quality_review:
            raise ConflictError("This sample has already been reviewed.")
        serializer = s.QualityReviewInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["verdict"] == m.ResultVerdict.PENDING:
            raise ValidationError({"verdict": ["A review must record a verdict, not pending."]})
        if data["verdict"] == m.ResultVerdict.PASS:
            # A pass must agree with the results on file. Checking the review
            # record itself is left out because it is being written now.
            blockers = [
                reason for reason in services.certificate_blockers(sample)
                if reason != "The quality review verdict is not pass."
            ]
            if blockers:
                raise ConflictError(" ".join(blockers))
        sample.quality_review = {
            "reviewer": services.actor_label(request.user),
            "at": timezone.now().isoformat(),
            "verdict": data["verdict"],
            "note": data.get("note", ""),
            "rule_version": rules.current_rule_version(),
        }
        services.transition_sample(sample, m.SampleStatus.REVIEWED if data["verdict"] != m.ResultVerdict.FAIL else m.SampleStatus.REJECTED)
        sample.audit = [*sample.audit, services.audit_entry(request.user, "reviewed", f"Verdict: {data['verdict']}")]
        sample.save(update_fields=["quality_review", "status", "audit", "updated_at"])
        return Response(s.SampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def certificate(self, request, pk=None):
        if not services.can_decide(request.user):
            raise PermissionDenied("Only Beldium quality operators may issue certificates.")
        sample = self.get_object()
        if sample.status != m.SampleStatus.REVIEWED:
            raise ConflictError("Only reviewed samples can be certified.")
        blockers = services.certificate_blockers(sample)
        if blockers:
            raise ConflictError(" ".join(blockers))
        # Validity is a Q&C setting (decision 4), read at the moment of issue.
        validity_days = rules.get("certificate_validity_days")
        cert = m.Certificate.objects.create(
            sample=sample,
            issued_by=services.actor_label(request.user),
            valid_until=timezone.now() + timezone.timedelta(days=validity_days),
            verification_hash=services.gen_hash(sample.reference, "certificate"),
            rule_version=rules.current_rule_version(),
        )
        services.transition_sample(sample, m.SampleStatus.CERTIFIED)
        sample.audit = [*sample.audit, services.audit_entry(request.user, "certified", cert.reference)]
        sample.save(update_fields=["status", "audit", "updated_at"])
        _notify_users([sample.registered_by, *_organisation_users(sample.buyer_organisation)], "Certificate issued", f"{cert.reference} was issued for {sample.reference}.", "certificate_issued", sample=sample, certificate=cert)
        return Response(s.CertificateSerializer(cert).data, status=status.HTTP_201_CREATED)


class CertificateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    audit_record_type = "verification_certificate"
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
    audit_record_type = "buyer_spec"
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
    audit_record_type = "ncr_capa"
    queryset = m.QualityNonConformity.objects.all()
    serializer_class = s.QualityNonConformitySerializer
    filterset_fields = ["status", "severity"]
    search_fields = ["reference", "title", "against"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        # The quality desk and oversight read every nonconformity. Anyone else
        # reads only the ones they raised before raising was limited to Q&C.
        # TODO(Q&C, Release 2): link a nonconformity to the responsible
        # organisation so that party can read it and submit the root cause.
        qs = self.queryset
        role = services.derive_role(self.request.user)
        if role == "operator":
            return qs
        if role == "regulator" and self.request.method in {"GET", "HEAD", "OPTIONS"}:
            return qs
        if self.request.method in {"GET", "HEAD", "OPTIONS"}:
            return qs.filter(raised_by_user=self.request.user)
        return qs.none()

    def create(self, request, *args, **kwargs):
        # Only Q&C raises and closes nonconformities.
        services.require_role(request.user, "operator")
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
        # Until a nonconformity names its responsible party (see get_queryset),
        # the quality desk records corrective actions on that party's behalf.
        services.require_role(request.user, "operator")
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
        services.require_role(request.user, "operator")
        nc = self.get_object()
        if nc.status == m.NCStatus.CLOSED:
            raise ConflictError("Cannot change a corrective action on a closed non-conformity.")
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
        services.require_role(request.user, "operator")
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
    audit_record_type = "notification"
    # Marking one's own notice as read is not a change to a Q&C record.
    audit_writes = False
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


# --- roles and role assignments (G3) -----------------------------------------


def _refuse(message, code, status_code=403):
    from common.exceptions import AppError

    raise AppError(message, code=code, status_code=status_code)


def _assignment_snapshot(assignment):
    return {
        "user_id": str(assignment.user_id), "role_code": assignment.role_id,
        "organisation_id": str(assignment.organisation_id) if assignment.organisation_id else None,
        "status": assignment.status, "valid_from": str(assignment.valid_from),
        "valid_to": str(assignment.valid_to) if assignment.valid_to else None,
    }


class QualityRoleViewSet(mixins.ListModelMixin, AtomicViewSet):
    """The 12 roles with their grid. Not paged: there are always 12."""

    audit_record_type = "users_and_roles"
    serializer_class = s.QualityRoleSerializer
    queryset = m.QualityRole.objects.all().prefetch_related("grants")
    pagination_class = None

    def list(self, request, *args, **kwargs):
        permissions.require(request.user, "users_and_roles", "read")
        return super().list(request, *args, **kwargs)


class QualityRoleAssignmentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    """Figure 5 row "Users and roles": Admin creates, Technical Lead updates,
    Q&C approves, CEO reads. An assignment gives no access until approved and
    is never deleted."""

    audit_record_type = "users_and_roles"
    serializer_class = s.RoleAssignmentSerializer
    queryset = m.QualityRoleAssignment.objects.all().select_related("user", "role", "organisation")
    filterset_fields = ["status"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        if not permissions.can(self.request.user, "users_and_roles", "read"):
            return self.queryset.none()
        qs = self.queryset
        params = self.request.query_params
        if params.get("user_id"):
            qs = qs.filter(user_id=params["user_id"])
        if params.get("role_code"):
            qs = qs.filter(role_id=params["role_code"])
        if params.get("organisation_id"):
            qs = qs.filter(organisation_id=params["organisation_id"])
        return qs

    def list(self, request, *args, **kwargs):
        permissions.require(request.user, "users_and_roles", "read")
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        from accounts.models import User
        from organisations.models import OrganisationMembership

        permissions.require(request.user, "users_and_roles", "create")
        serializer = s.CreateRoleAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = User.objects.filter(pk=data["user_id"], is_active=True).first()
        if target is None:
            raise ValidationError({"user_id": ["Select an active user."]})
        if target.pk == request.user.pk:
            _refuse("You cannot give a role to yourself.", "self_assignment")
        role = m.QualityRole.objects.get(pk=data["role_code"])
        organisation = None
        if data.get("organisation_id"):
            organisation = Organisation.objects.filter(pk=data["organisation_id"]).first()
            if organisation is None:
                raise ValidationError({"organisation_id": ["Select a registered organisation."]})
        if role.code in role_data.ORGANISATION_ROLES:
            if organisation is None:
                _refuse("This role needs an organisation.", "organisation_required", 400)
            if organisation.verification_status != "verified":
                _refuse("The organisation is not verified, or is suspended.", "organisation_not_verified", 409)
            if not OrganisationMembership.objects.filter(user=target, organisation=organisation, is_active=True).exists():
                _refuse("The user is not an active member of this organisation.", "not_a_member", 409)
        elif organisation is not None:
            raise ValidationError({"organisation_id": ["An internal role is not tied to an organisation."]})
        open_statuses = [m.RoleAssignmentStatus.PENDING_APPROVAL, m.RoleAssignmentStatus.ACTIVE]
        if m.QualityRoleAssignment.objects.filter(
            user=target, role=role, organisation=organisation, status__in=open_statuses,
        ).exists():
            _refuse("This user already holds this role for this organisation.", "assignment_exists", 409)
        assignment = m.QualityRoleAssignment.objects.create(
            user=target, role=role, organisation=organisation,
            valid_from=data["valid_from"], valid_to=data.get("valid_to"), granted_by=request.user,
        )
        self.audit_detail = {"record_label": f"{role.code} for {target.email}", "after": _assignment_snapshot(assignment)}
        return Response(self.get_serializer(assignment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        permissions.require(request.user, "users_and_roles", "approve")
        assignment = self.get_object()
        if request.user.pk in {assignment.granted_by_id, assignment.user_id}:
            _refuse("You cannot approve something you submitted, or your own role.", "same_person")
        if assignment.status != m.RoleAssignmentStatus.PENDING_APPROVAL:
            _refuse("This assignment has already been decided.", "already_decided", 409)
        serializer = s.DecideRoleAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = _assignment_snapshot(assignment)
        approved = serializer.validated_data["decision"] == "approve"
        assignment.status = m.RoleAssignmentStatus.ACTIVE if approved else m.RoleAssignmentStatus.REJECTED
        assignment.approved_by = request.user
        assignment.decided_at = timezone.now()
        assignment.decision_reason = serializer.validated_data.get("reason", "")
        assignment.save(update_fields=["status", "approved_by", "decided_at", "decision_reason", "updated_at"])
        self.audit_detail = {
            "record_label": f"{assignment.role_id} for {assignment.user.email}", "before": before,
            "after": _assignment_snapshot(assignment), "reason": assignment.decision_reason,
        }
        return Response(self.get_serializer(assignment).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        permissions.require(request.user, "users_and_roles", "update")
        assignment = self.get_object()
        if assignment.status == m.RoleAssignmentStatus.ENDED:
            _refuse("This assignment has already ended.", "already_ended", 409)
        if assignment.status != m.RoleAssignmentStatus.ACTIVE:
            _refuse("Only an active assignment can be ended.", "not_active", 409)
        serializer = s.EndRoleAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = _assignment_snapshot(assignment)
        today = timezone.localdate()
        assignment.status = m.RoleAssignmentStatus.ENDED
        assignment.valid_to = today if assignment.valid_to is None or assignment.valid_to > today else assignment.valid_to
        assignment.ended_by = request.user
        assignment.ended_at = timezone.now()
        assignment.end_reason = serializer.validated_data["reason"]
        assignment.save(update_fields=["status", "valid_to", "ended_by", "ended_at", "end_reason", "updated_at"])
        self.audit_detail = {
            "record_label": f"{assignment.role_id} for {assignment.user.email}", "before": before,
            "after": _assignment_snapshot(assignment), "reason": assignment.end_reason,
        }
        return Response(self.get_serializer(assignment).data)


# --- audit log read (G4) ------------------------------------------------------


class QualityAuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    """Read only. There is no create, update or delete route. Beldium Admin is
    refused (decision 8, rule AT-05). Reading the log is itself logged."""

    audit_record_type = "audit_log"
    serializer_class = s.QualityAuditEntrySerializer
    queryset = m.QualityAuditEntry.objects.all()
    http_method_names = ["get", "head", "options"]
    MAX_RANGE_DAYS = 366

    def _require_access(self):
        permissions.require(
            self.request.user, "audit_log", "read",
            message="Your role does not have access to the audit log.", code="audit_log_closed",
        )

    def get_queryset(self):
        self._require_access()
        qs = self.queryset
        params = self.request.query_params
        for field in ("record_type", "record_id", "batch_id", "actor_id", "action", "outcome"):
            if params.get(field):
                qs = qs.filter(**{field: params[field]})
        start = _parse_when(params.get("from"), "from")
        end = _parse_when(params.get("to"), "to")
        if start and end and (end - start).days > self.MAX_RANGE_DAYS:
            from common.exceptions import AppError
            raise AppError("Ask for 366 days or fewer at a time.", code="date_range_too_wide")
        if start:
            qs = qs.filter(occurred_at__gte=start)
        if end:
            qs = qs.filter(occurred_at__lte=end)
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        audit_log.record(
            actor=request.user, action="audit_log.read", record_type="audit_log",
            reason=request.META.get("QUERY_STRING", "")[:500], request=request,
        )
        return response

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        audit_log.record(
            actor=request.user, action="audit_log.read", record_type="audit_log",
            record_id=kwargs.get("pk", ""), request=request,
        )
        return response


def _parse_when(value, field):
    if not value:
        return None
    from django.utils.dateparse import parse_date, parse_datetime

    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is None:
            raise ValidationError({field: ["Use a date (YYYY-MM-DD) or an ISO 8601 time."]})
        from datetime import datetime, time

        parsed = datetime.combine(day, time.max if field == "to" else time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


# --- settings (G5) ------------------------------------------------------------

SETTINGS_READERS = ("QC", "CEO", "TL")
SETTINGS_WRITERS = ("QC",)


class QualitySettingViewSet(AtomicViewSet):
    """Q&C rule settings. The grid has no row for settings, so who reads and
    changes them follows the API contract: Q&C, CEO and the Technical Lead
    read, Q&C changes. There is no PATCH and no DELETE: a value changes only
    by a new change row.
    TODO(Q&C Partner): confirm who may read and change settings."""

    audit_record_type = "setting"
    queryset = m.QualitySetting.objects.all()
    lookup_field = "key"
    lookup_value_regex = "[a-z0-9_]+"
    http_method_names = ["get", "post", "head", "options"]

    def _require(self, codes):
        if not permissions.has_role(self.request.user, *codes):
            _refuse("You do not have permission to perform this action.", "permission_denied")

    def _row(self, setting, now):
        change = rules._effective_change(setting.key, now)
        pending = (
            m.QualitySettingChange.objects.filter(setting=setting, effective_from__gt=now)
            .order_by("effective_from").first()
        )
        return {
            "setting_key": setting.key,
            "label": setting.label,
            "value": change.value if change else None,
            "unit": setting.unit or None,
            "value_type": setting.value_type,
            "effective_from": change.effective_from if change else None,
            "is_placeholder": change is None or change.value is None,
            "owner": setting.owner,
            "note": setting.note,
            "rule_version": change.rule_version_id if change else None,
            "pending_change": {"value": pending.value, "effective_from": pending.effective_from} if pending else None,
        }

    def list(self, request, *args, **kwargs):
        self._require(SETTINGS_READERS)
        now = timezone.now()
        version = m.QualityRuleVersion.objects.filter(effective_from__lte=now).order_by("-number").first()
        return Response({
            "rule_version": str(version.number) if version else "",
            "rule_version_effective_from": version.effective_from if version else None,
            "results": [self._row(setting, now) for setting in self.get_queryset()],
        })

    def retrieve(self, request, *args, **kwargs):
        self._require(SETTINGS_READERS)
        return Response(self._row(self.get_object(), timezone.now()))

    @action(detail=True, methods=["get", "post"])
    def changes(self, request, key=None):
        if request.method == "GET":
            self._require(SETTINGS_READERS)
            setting = self.get_object()
            page = self.paginate_queryset(setting.changes.select_related("setting").order_by("-effective_from", "-changed_at"))
            return self.get_paginated_response(s.SettingChangeSerializer(page, many=True).data)
        self._require(SETTINGS_WRITERS)
        setting = self.get_object()
        serializer = s.NewSettingChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        before = rules._effective_change(setting.key)
        row = rules.change(
            setting, value=data["value"], effective_from=data["effective_from"],
            reason=data["reason"], user=request.user,
        )
        self.audit_detail = {
            "record_label": setting.key,
            "before": {"value": before.value if before else None},
            "after": {"value": row.value, "effective_from": row.effective_from.isoformat(), "rule_version": row.rule_version_id},
            "reason": row.reason,
        }
        return Response(s.SettingChangeSerializer(row).data, status=status.HTTP_201_CREATED)
