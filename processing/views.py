"""API for the Processing Compliance register.

Read scope is decided in ``get_queryset`` from the caller's audience: the
operator desk and the regulator see the whole register, a processor sees only
its own records. Write scope is decided by the permission classes. Neither
reads a role from the request, so a client cannot widen its own access.
"""
from collections import OrderedDict
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponseRedirect
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AccountAuditEvent
from common.exceptions import AppError, ConflictError
from processing import audit, checklist, scoring
from processing.models import (
    EXPIRY_WARNING_DAYS,
    ApplicationDecision,
    ApplicationSection,
    ApplicationStage,
    ComplianceReport,
    EnvironmentalAlert,
    Incident,
    Inspection,
    NonConformity,
    NonConformityEvidence,
    ProcessingApplication,
    ProcessingDocument,
    ProcessingType,
    Processor,
    ProcessorStatus,
    ReviewState,
    SectionKey,
    TraceabilityRun,
)
from processing.permissions import (
    OPERATOR,
    REGULATOR,
    IsProcessingOperator,
    IsProcessingParticipant,
    audience,
    can_decide,
    is_operator,
    can_edit_application,
    capabilities,
    organisation_ids,
    owns_processor,
)
from processing.serializers import (
    AlertStatusSerializer,
    ProcessingDecisionSerializer,
    ApplicationSectionSerializer,
    ComplianceReportSerializer,
    DashboardSerializer,
    ProcessingDocumentReviewSerializer,
    EnvironmentalAlertSerializer,
    ExpiringDocumentSerializer,
    FacilitySerializer,
    IncidentSerializer,
    InspectionRequestSerializer,
    InspectionSerializer,
    NonConformityClosureSerializer,
    NonConformityEvidenceSerializer,
    NonConformitySerializer,
    ProcessingApplicationDetailSerializer,
    ProcessingApplicationSerializer,
    ProcessingAuditEventSerializer,
    ProcessingDocumentSerializer,
    ProcessorDetailSerializer,
    ProcessorSerializer,
    RiskCauseSerializer,
    ProcessingSectionReviewSerializer,
    TraceabilityRunSerializer,
)

# Stages an applicant may still edit their own submission in. Once the desk has
# taken a decision, corrections go through a new information request.
EDITABLE_STAGES = {ApplicationStage.NEW, ApplicationStage.AWAITING_INFO}

DECISION_STATUS = {
    ApplicationDecision.APPROVED: ProcessorStatus.APPROVED,
    ApplicationDecision.CONDITIONAL: ProcessorStatus.CONDITIONAL,
    ApplicationDecision.REJECTED: ProcessorStatus.SUSPENDED,
}


def serve_file(stored_file, filename):
    """Hand back a stored file without exposing the storage layer.

    An S3 URL is already signed, access controlled and self-expiring, so a
    redirect is both cheaper and safer than proxying bytes. A local path has
    nothing guarding it, so those bytes are streamed through this view, which
    has already checked the caller.
    """
    url = stored_file.url
    if url.startswith(("http://", "https://")):
        return HttpResponseRedirect(url)
    return FileResponse(stored_file.open("rb"), as_attachment=True, filename=filename)


class ProcessingViewSetMixin:
    """Shared scoping, auditing and transaction handling."""

    permission_classes = [IsProcessingOperator]

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        # DRF turns exceptions into responses, so a failed action would
        # otherwise commit whatever it wrote before raising.
        response = super().dispatch(request, *args, **kwargs)
        if response.status_code >= 400:
            transaction.set_rollback(True)
        return response

    @property
    def audience(self):
        return audience(self.request.user)

    def sees_whole_register(self):
        return self.audience in {OPERATOR, REGULATOR}

    def record(self, action_name, *, target="", detail="", **metadata):
        return audit.record(self.request, action_name, target=target, detail=detail, **metadata)


class ProcessorViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    """The register of processing companies."""

    serializer_class = ProcessorSerializer
    queryset = Processor.objects.none()
    filterset_fields = ["status", "processing_type", "state", "region"]
    search_fields = ["name", "rc_number", "tin", "reference"]
    ordering_fields = ["name", "compliance_score", "registered_on", "last_inspection_on"]
    ordering = ["name"]

    def get_serializer_class(self):
        return ProcessorDetailSerializer if self.action == "retrieve" else ProcessorSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Processor.objects.select_related("organisation").annotate(
            facilities_count=Count("facilities", distinct=True),
            open_non_conformities=Count(
                "non_conformities",
                filter=~Q(non_conformities__status=NonConformity.Status.CLOSED),
                distinct=True,
            ),
        )
        # Aggregation introduces a GROUP BY, which drops the model's default
        # ordering; without restoring it pagination is non-deterministic.
        qs = qs.order_by("name")
        if self.action == "retrieve":
            qs = qs.prefetch_related("facilities")
        if self.sees_whole_register():
            return qs
        return qs.filter(organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        processor = serializer.save()
        self.record("processor_registered", target=processor.name, detail=f"Registered as {processor.get_processing_type_display()}.", processor_id=str(processor.id))

    def perform_update(self, serializer):
        processor = serializer.save()
        self.record("processor_updated", target=processor.name, detail=f"Status {processor.get_status_display()}, score {processor.compliance_score}.", processor_id=str(processor.id))

    @extend_schema(methods=["GET"], responses=FacilitySerializer(many=True))
    @extend_schema(methods=["POST"], request=FacilitySerializer, responses={201: FacilitySerializer})
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def facilities(self, request, pk=None):
        processor = self.get_object()
        if request.method == "GET":
            return Response(FacilitySerializer(processor.facilities.all(), many=True).data)
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can add a facility.")
        serializer = FacilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        facility = serializer.save(processor=processor)
        self.record("facility_added", target=f"{processor.name} · {facility.name}", detail=f"Facility registered in {facility.state or 'unspecified state'}.", processor_id=str(processor.id))
        return Response(FacilitySerializer(facility).data, status=status.HTTP_201_CREATED)


class ProcessingApplicationViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    """Processor admission applications and their ten evidence sections."""

    serializer_class = ProcessingApplicationSerializer
    queryset = ProcessingApplication.objects.none()
    permission_classes = [IsProcessingParticipant]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    # DELETE is allowed so the risk-cause sub-resource can be reached; the
    # application itself is not deletable — see `destroy` below.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["stage", "decision", "processing_type", "state", "processor"]
    search_fields = ["company", "reference", "rc_number", "state", "facility_name"]
    ordering_fields = ["submitted_on", "created_at", "company"]
    ordering = ["-submitted_on", "-created_at"]

    def get_serializer_class(self):
        return ProcessingApplicationDetailSerializer if self.action == "retrieve" else ProcessingApplicationSerializer

    @extend_schema(exclude=True)
    def destroy(self, request, *args, **kwargs):
        """An application is a compliance record; it is never deleted."""
        raise MethodNotAllowed("DELETE")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = (
            ProcessingApplication.objects.select_related("processor", "organisation")
            .prefetch_related("risk_causes", "documents", "non_conformities", "sections")
        )
        if self.sees_whole_register():
            return qs
        ids = organisation_ids(self.request.user)
        return qs.filter(
            Q(organisation_id__in=ids) | Q(processor__organisation_id__in=ids) | Q(created_by=self.request.user)
        ).distinct()

    def get_object(self):
        application = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            # Two reviewers acting on the same application would otherwise
            # interleave a stage change with a decision.
            return (
                ProcessingApplication.objects.select_for_update()
                .select_related("processor", "organisation")
                .get(pk=application.pk)
            )
        return application

    def _assert_owns_subject(self, data):
        """An applicant files for its own organisation, never for another's.

        Naming neither is fine and is the ordinary first-time case: a company
        applying to join the register has no processor record yet, and its
        organisation is inferred below. Only *naming someone else* is refused.
        """
        if is_operator(self.request.user):
            return
        allowed = set(organisation_ids(self.request.user))
        organisation_id = getattr(data.get("organisation"), "id", None)
        if organisation_id and organisation_id not in allowed:
            raise PermissionDenied("You cannot file an application for another organisation.")
        processor = data.get("processor")
        if processor and not owns_processor(self.request.user, processor):
            raise PermissionDenied("You cannot file an application for another processor.")

    def _own_organisation(self):
        """The single organisation to attribute an unattributed application to.

        With more than one membership the applicant has to say which, rather
        than have the server guess and file against the wrong company.
        """
        ids = organisation_ids(self.request.user)
        if len(ids) == 1:
            return ids[0]
        return None

    def assert_editor(self, application):
        if not can_edit_application(self.request.user, application):
            raise PermissionDenied("Your role cannot edit this application.")

    def assert_editable(self, application):
        if application.stage not in EDITABLE_STAGES:
            raise ConflictError(
                "This application is not open for edits; the desk has to request information first.",
                code="application_locked",
            )

    def assert_reviewer(self):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can review an application.")

    def perform_create(self, serializer):
        self._assert_owns_subject(serializer.validated_data)
        extra = {}
        if not serializer.validated_data.get("organisation") and not is_operator(self.request.user):
            organisation_id = self._own_organisation()
            if organisation_id is None:
                raise AppError(
                    "Name the organisation this application is for.",
                    code="organisation_required",
                )
            extra["organisation_id"] = organisation_id
        application = serializer.save(created_by=self.request.user, **extra)
        # Every application carries all ten sections from the start, so the
        # checklist is complete on the first read rather than appearing as the
        # applicant fills it in.
        ApplicationSection.objects.bulk_create(
            [ApplicationSection(application=application, key=key) for key, _ in SectionKey.choices]
        )
        self.record("application_created", target=application.reference, detail=f"{application.company} started a processing application.", application_id=str(application.id))

    def perform_update(self, serializer):
        self.assert_editor(serializer.instance)
        self.assert_editable(serializer.instance)
        application = serializer.save()
        self.record("application_updated", target=application.reference, detail="Applicant updated the submission header.", application_id=str(application.id))

    @extend_schema(
        request=ApplicationSectionSerializer,
        responses=ApplicationSectionSerializer,
        parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["patch"], url_path=r"sections/(?P<key>[a-z_]+)", url_name="section")
    def section(self, request, pk=None, key=None):
        """Applicant-side save of one evidence section."""
        application = self.get_object()
        self.assert_editor(application)
        self.assert_editable(application)
        section = application.sections.filter(key=key).first()
        if not section:
            raise AppError("Unknown application section.", code="unknown_section", status_code=404)
        serializer = ApplicationSectionSerializer(section, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        # A re-submitted section goes back into the queue: a verdict on the
        # previous content says nothing about the new content.
        serializer.save(review_state=ReviewState.PENDING, review_note="", reviewed_by=None, reviewed_at=None)
        self.record("section_saved", target=f"{application.reference} · {SectionKey(key).label}", detail="Applicant updated the section.", application_id=str(application.id), section=key)
        return Response(ApplicationSectionSerializer(section, context={"request": request}).data)

    @extend_schema(
        request=ProcessingSectionReviewSerializer,
        responses=ApplicationSectionSerializer,
        parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["post"], url_path=r"sections/(?P<key>[a-z_]+)/review", url_name="review-section")
    def review_section(self, request, pk=None, key=None):
        """Operator-side verdict on one evidence section."""
        application = self.get_object()
        self.assert_reviewer()
        if application.stage == ApplicationStage.DECIDED:
            raise ConflictError("This application has already been decided.", code="application_decided")
        section = application.sections.filter(key=key).first()
        if not section:
            raise AppError("Unknown application section.", code="unknown_section", status_code=404)
        serializer = ProcessingSectionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section.review_state = serializer.validated_data["review_state"]
        section.review_note = serializer.validated_data.get("note", "")
        section.reviewed_by = request.user
        section.reviewed_at = timezone.now()
        section.save(update_fields=["review_state", "review_note", "reviewed_by", "reviewed_at", "updated_at"])

        # Reviewing anything moves a new application into the queue; asking for
        # information hands it back to the applicant.
        if section.review_state == ReviewState.INFO_REQUESTED:
            self._set_stage(application, ApplicationStage.AWAITING_INFO)
        elif application.stage == ApplicationStage.NEW:
            self._set_stage(application, ApplicationStage.IN_REVIEW)

        self.record("section_reviewed", target=f"{application.reference} · {SectionKey(key).label}", detail=f"Marked {section.get_review_state_display().lower()}.", application_id=str(application.id), section=key, review_state=section.review_state)
        return Response(ApplicationSectionSerializer(section, context={"request": request}).data)

    def _set_stage(self, application, stage):
        if application.stage == stage:
            return
        application.stage = stage
        application.save(update_fields=["stage", "updated_at"])

    @extend_schema(methods=["GET"], responses=ProcessingDocumentSerializer(many=True))
    @extend_schema(methods=["POST"], request=ProcessingDocumentSerializer, responses={201: ProcessingDocumentSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[JSONParser, MultiPartParser, FormParser], pagination_class=None)
    def documents(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(
                ProcessingDocumentSerializer(application.documents.all(), many=True, context={"request": request}).data
            )
        self.assert_editor(application)
        self.assert_editable(application)
        # A re-upload replaces the evidence rather than leaving two rows the
        # desk would have to choose between, and clears the previous verdict:
        # a verdict on the old file says nothing about the new one.
        existing = application.documents.filter(
            section=request.data.get("section", ""), name=request.data.get("name", "")
        ).first()
        serializer = ProcessingDocumentSerializer(existing, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        document = serializer.save(
            application=application,
            processor=application.processor,
            uploaded_by=request.user,
            review_state=ReviewState.PENDING,
            review_note="",
            reviewed_by=None,
            reviewed_at=None,
        )
        self.record("document_uploaded", target=f"{application.reference} · {document.name}", detail=f"Uploaded against {SectionKey(document.section).label}.", application_id=str(application.id), document_id=str(document.id))
        return Response(
            ProcessingDocumentSerializer(document, context={"request": request}).data,
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )

    @extend_schema(methods=["GET"], responses=RiskCauseSerializer(many=True))
    @extend_schema(methods=["POST"], request=RiskCauseSerializer, responses={201: RiskCauseSerializer})
    @action(detail=True, methods=["get", "post"], url_path="risk-causes", url_name="risk-causes", pagination_class=None)
    def risk_causes(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(RiskCauseSerializer(application.risk_causes.all(), many=True).data)
        self.assert_reviewer()
        serializer = RiskCauseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cause = serializer.save(application=application)
        self.record("risk_cause_added", target=application.reference, detail=f"{cause.cause} (+{cause.weight}).", application_id=str(application.id))
        return Response(RiskCauseSerializer(cause).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={204: None}, parameters=[OpenApiParameter("cause_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["delete"], url_path=r"risk-causes/(?P<cause_id>[^/.]+)", url_name="risk-cause-detail")
    def risk_cause_detail(self, request, pk=None, cause_id=None):
        application = self.get_object()
        self.assert_reviewer()
        cause = application.risk_causes.filter(id=cause_id).first()
        if not cause:
            raise AppError("Risk cause not found.", code="not_found", status_code=404)
        label = cause.cause
        cause.delete()
        self.record("risk_cause_removed", target=application.reference, detail=f"{label} withdrawn.", application_id=str(application.id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses=ProcessingApplicationDetailSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Applicant hands the submission to the desk."""
        application = self.get_object()
        self.assert_editor(application)
        if application.stage not in EDITABLE_STAGES:
            raise ConflictError("This application has already been submitted.", code="application_not_editable")
        percent = scoring.completeness(application)
        if percent != 100:
            raise AppError(
                "Complete every evidence section before submitting.",
                code="application_incomplete",
                status_code=409,
                details={"completeness": percent, "outstanding": scoring.outstanding(application)},
            )
        application.stage = ApplicationStage.IN_REVIEW
        application.submitted_on = application.submitted_on or timezone.localdate()
        application.save(update_fields=["stage", "submitted_on", "updated_at"])
        self.record("application_submitted", target=application.reference, detail=f"{application.company} submitted for review.", application_id=str(application.id))
        return Response(ProcessingApplicationDetailSerializer(application, context={"request": request}).data)

    @extend_schema(request=InspectionRequestSerializer, responses={201: InspectionSerializer})
    @action(detail=True, methods=["post"], url_path="request-inspection", url_name="request-inspection")
    def request_inspection(self, request, pk=None):
        application = self.get_object()
        self.assert_reviewer()
        if application.stage == ApplicationStage.DECIDED:
            raise ConflictError("This application has already been decided.", code="application_decided")
        serializer = InspectionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        inspection = Inspection.objects.create(
            application=application,
            processor=application.processor,
            facility_name=application.facility_name,
            state=application.state,
            scheduled_for=serializer.validated_data.get("scheduled_for"),
            inspection_type=serializer.validated_data["inspection_type"],
            inspector_name=serializer.validated_data.get("inspector_name", ""),
            status=Inspection.Status.SCHEDULED if serializer.validated_data.get("scheduled_for") else Inspection.Status.REQUESTED,
            outcome="",
            requested_by=request.user,
        )
        self._set_stage(application, ApplicationStage.INSPECTION)
        self.record("inspection_requested", target=f"{application.reference} · {inspection.reference}", detail=serializer.validated_data.get("note") or "Physical inspection requested.", application_id=str(application.id), inspection_id=str(inspection.id))
        return Response(InspectionSerializer(inspection).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=ProcessingDecisionSerializer, responses=ProcessingApplicationDetailSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        """Record the desk's decision and reflect it on the processor record."""
        application = self.get_object()
        self.assert_reviewer()
        if application.stage == ApplicationStage.DECIDED:
            raise ConflictError("This application has already been decided.", code="application_decided")
        serializer = ProcessingDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        decision = serializer.validated_data["decision"]

        if decision == ApplicationDecision.MORE_INFO:
            # Not an outcome: the application goes back to the applicant, and
            # the desk decides again once the gaps are filled.
            application.stage = ApplicationStage.AWAITING_INFO
            application.decision_note = serializer.validated_data.get("note", "")
            application.save(update_fields=["stage", "decision_note", "updated_at"])
            self.record("information_requested", target=application.reference, detail=application.decision_note or "Further information requested.", application_id=str(application.id))
            return Response(ProcessingApplicationDetailSerializer(application, context={"request": request}).data)

        if decision == ApplicationDecision.APPROVED:
            unresolved = [s for s in application.sections.all() if s.review_state != ReviewState.VERIFIED]
            if unresolved:
                raise ConflictError(
                    "Verify every evidence section before approving.",
                    code="sections_not_verified",
                )
            if application.non_conformities.exclude(status=NonConformity.Status.CLOSED).exists():
                raise ConflictError("Close every open finding before approving.", code="non_conformities_open")

        application.decision = decision
        application.decision_note = serializer.validated_data.get("note", "")
        application.decided_by = request.user
        application.decided_at = timezone.now()
        application.stage = ApplicationStage.DECIDED
        application.save(update_fields=["decision", "decision_note", "decided_by", "decided_at", "stage", "updated_at"])

        processor = application.processor
        if processor:
            processor.status = DECISION_STATUS[decision]
            processor.compliance_score = max(0, 100 - scoring.risk_score(application))
            processor.save(update_fields=["status", "compliance_score", "updated_at"])

        self.record("application_decided", target=application.reference, detail=f"{application.get_decision_display()}. {application.decision_note}".strip(), application_id=str(application.id), decision=decision)
        return Response(ProcessingApplicationDetailSerializer(application, context={"request": request}).data)

    @extend_schema(responses=ProcessingAuditEventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        application = self.get_object()
        events = AccountAuditEvent.objects.filter(
            event_type__startswith=audit.PREFIX, metadata__application_id=str(application.id)
        ).select_related("actor")
        page = self.paginate_queryset(events)
        serializer = ProcessingAuditEventSerializer(page if page is not None else events, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class NonConformityViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    """Findings raised against an application section or a live processor."""

    serializer_class = NonConformitySerializer
    queryset = NonConformity.objects.none()
    permission_classes = [IsProcessingParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "severity", "section", "application", "processor"]
    search_fields = ["reference", "title", "detail"]
    ordering_fields = ["raised_on", "due_on", "severity"]
    ordering = ["-raised_on", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = NonConformity.objects.select_related("application", "processor").prefetch_related("evidence")
        if self.sees_whole_register():
            return qs
        ids = organisation_ids(self.request.user)
        return qs.filter(
            Q(processor__organisation_id__in=ids)
            | Q(application__organisation_id__in=ids)
            | Q(application__created_by=self.request.user)
        ).distinct()

    def perform_create(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can raise a finding.")
        finding = serializer.save(raised_by=self.request.user)
        self.record("non_conformity_raised", target=finding.reference, detail=f"{finding.get_severity_display()} finding: {finding.title}", application_id=str(finding.application_id or ""), non_conformity_id=str(finding.id))

    def perform_update(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can amend a finding.")
        finding = serializer.save()
        self.record("non_conformity_updated", target=finding.reference, detail=finding.title, non_conformity_id=str(finding.id))

    @extend_schema(request=NonConformityEvidenceSerializer, responses={201: NonConformityEvidenceSerializer})
    @action(detail=True, methods=["post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def evidence(self, request, pk=None):
        """Corrective-action evidence, submitted by the processor."""
        finding = self.get_object()
        if finding.status == NonConformity.Status.CLOSED:
            raise ConflictError("This finding is already closed.", code="non_conformity_closed")
        serializer = NonConformityEvidenceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        evidence = serializer.save(non_conformity=finding, submitted_by=request.user)
        finding.status = NonConformity.Status.EVIDENCE_SUBMITTED
        finding.save(update_fields=["status", "updated_at"])
        self.record("evidence_submitted", target=finding.reference, detail=evidence.name, non_conformity_id=str(finding.id))
        return Response(
            NonConformityEvidenceSerializer(evidence, context={"request": request}).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(request=NonConformityClosureSerializer, responses=NonConformitySerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Accept the evidence and close, or reject it and reopen."""
        finding = self.get_object()
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can close a finding.")
        serializer = NonConformityClosureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        accept = serializer.validated_data["accept"]
        if accept and not finding.evidence.exists():
            raise ConflictError("No corrective-action evidence has been submitted.", code="evidence_required")
        finding.status = NonConformity.Status.CLOSED if accept else NonConformity.Status.OPEN
        finding.closure_note = serializer.validated_data.get("note", "")
        finding.closed_by = request.user if accept else None
        finding.closed_at = timezone.now() if accept else None
        finding.save(update_fields=["status", "closure_note", "closed_by", "closed_at", "updated_at"])
        self.record(
            "non_conformity_closed" if accept else "non_conformity_reopened",
            target=finding.reference,
            detail=finding.closure_note or ("Evidence accepted." if accept else "Evidence rejected; finding reopened."),
            non_conformity_id=str(finding.id),
        )
        return Response(NonConformitySerializer(finding, context={"request": request}).data)

    @extend_schema(parameters=[OpenApiParameter("evidence_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["get"], url_path=r"evidence/(?P<evidence_id>[^/.]+)/download", url_name="download-evidence")
    def download_evidence(self, request, pk=None, evidence_id=None):
        finding = self.get_object()
        evidence = NonConformityEvidence.objects.filter(id=evidence_id, non_conformity=finding).first()
        if not evidence or not evidence.file:
            raise AppError("Evidence file not found.", code="not_found", status_code=404)
        return serve_file(evidence.file, evidence.original_name)


class InspectionViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    serializer_class = InspectionSerializer
    queryset = Inspection.objects.none()
    permission_classes = [IsProcessingParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "inspection_type", "state", "processor", "application"]
    search_fields = ["reference", "facility_name", "inspector_name"]
    ordering_fields = ["scheduled_for", "created_at"]
    ordering = ["-scheduled_for", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Inspection.objects.select_related("application", "processor", "inspector")
        if self.sees_whole_register():
            return qs
        ids = organisation_ids(self.request.user)
        return qs.filter(Q(processor__organisation_id__in=ids) | Q(application__organisation_id__in=ids)).distinct()

    def _assert_desk(self):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can schedule inspections.")

    def perform_create(self, serializer):
        self._assert_desk()
        inspection = serializer.save(requested_by=self.request.user)
        self.record("inspection_scheduled", target=inspection.reference, detail=f"{inspection.get_inspection_type_display()} at {inspection.facility_name or 'facility'}.", inspection_id=str(inspection.id), application_id=str(inspection.application_id or ""))

    def perform_update(self, serializer):
        self._assert_desk()
        inspection = serializer.save()
        if inspection.status == Inspection.Status.COMPLETED and not inspection.completed_at:
            inspection.completed_at = timezone.now()
            inspection.save(update_fields=["completed_at", "updated_at"])
            if inspection.processor_id:
                inspection.processor.last_inspection_on = timezone.localdate()
                inspection.processor.save(update_fields=["last_inspection_on", "updated_at"])
        self.record("inspection_updated", target=inspection.reference, detail=inspection.outcome or inspection.get_status_display(), inspection_id=str(inspection.id), application_id=str(inspection.application_id or ""))


class EnvironmentalAlertViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EnvironmentalAlertSerializer
    queryset = EnvironmentalAlert.objects.none()
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "severity", "state", "processor"]
    search_fields = ["reference", "parameter", "facility_name"]
    ordering_fields = ["detected_on", "severity"]
    ordering = ["-detected_on", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = EnvironmentalAlert.objects.select_related("processor", "facility")
        if self.sees_whole_register():
            return qs
        return qs.filter(processor__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        alert = serializer.save()
        self.record("environmental_alert_raised", target=alert.reference, detail=f"{alert.parameter} at {alert.reading} against {alert.threshold}.", alert_id=str(alert.id))

    @extend_schema(request=AlertStatusSerializer, responses=EnvironmentalAlertSerializer)
    @action(detail=True, methods=["post"], url_path="status", url_name="set-status")
    def set_status(self, request, pk=None):
        alert = self.get_object()
        serializer = AlertStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = serializer.validated_data["status"]
        if alert.status == EnvironmentalAlert.Status.RESOLVED:
            raise ConflictError("This alert is already resolved.", code="alert_resolved")
        alert.status = target
        alert.resolution_note = serializer.validated_data.get("note", alert.resolution_note)
        if target == EnvironmentalAlert.Status.ACKNOWLEDGED:
            alert.acknowledged_by = request.user
            alert.acknowledged_at = timezone.now()
        else:
            alert.resolved_at = timezone.now()
        alert.save(update_fields=["status", "resolution_note", "acknowledged_by", "acknowledged_at", "resolved_at", "updated_at"])
        self.record(f"environmental_alert_{target}", target=alert.reference, detail=alert.resolution_note or f"{alert.parameter} {target}.", alert_id=str(alert.id))
        return Response(EnvironmentalAlertSerializer(alert).data)


class IncidentViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    serializer_class = IncidentSerializer
    queryset = Incident.objects.none()
    permission_classes = [IsProcessingParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "severity", "state", "processor"]
    search_fields = ["reference", "incident_type", "facility_name", "summary"]
    ordering_fields = ["reported_on", "severity"]
    ordering = ["-reported_on", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Incident.objects.select_related("processor", "facility")
        if self.sees_whole_register():
            return qs
        return qs.filter(processor__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        # A processor reports its own incidents; that is the point of the
        # register, so this is deliberately not desk-only — but only its own.
        if not owns_processor(self.request.user, serializer.validated_data.get("processor")):
            raise PermissionDenied("You can only report incidents at your own facilities.")
        incident = serializer.save(reported_by=self.request.user)
        self.record("incident_reported", target=incident.reference, detail=f"{incident.get_severity_display()} · {incident.incident_type}", incident_id=str(incident.id))

    def perform_update(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can update an incident record.")
        incident = serializer.save()
        if incident.status == Incident.Status.CLOSED and not incident.closed_at:
            incident.closed_at = timezone.now()
            incident.save(update_fields=["closed_at", "updated_at"])
        self.record("incident_updated", target=incident.reference, detail=incident.get_status_display(), incident_id=str(incident.id))


class TraceabilityRunViewSet(ProcessingViewSetMixin, viewsets.ModelViewSet):
    serializer_class = TraceabilityRunSerializer
    queryset = TraceabilityRun.objects.none()
    permission_classes = [IsProcessingParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["processor", "facility", "qc_verdict"]
    search_fields = ["reference", "input_batch", "output_batch", "input_source"]
    ordering_fields = ["started_at", "completed_at"]
    ordering = ["-started_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = TraceabilityRun.objects.select_related("processor", "facility")
        if self.sees_whole_register():
            return qs
        return qs.filter(processor__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_processor(self.request.user, serializer.validated_data.get("processor")):
            raise PermissionDenied("You can only record runs for your own facilities.")
        run = serializer.save()
        self.record("run_recorded", target=run.reference, detail=f"{run.input_batch} → {run.output_batch} at {run.yield_percent}% yield.", run_id=str(run.id))


class ProcessingDocumentViewSet(ProcessingViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read, review and download evidence; uploads go through the application."""

    serializer_class = ProcessingDocumentSerializer
    queryset = ProcessingDocument.objects.none()
    permission_classes = [IsProcessingParticipant]
    filterset_fields = ["section", "application", "processor"]
    search_fields = ["name", "reference", "issuer"]
    ordering_fields = ["expires_on", "issued_on", "name"]
    ordering = ["section", "name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = ProcessingDocument.objects.select_related("application", "processor")
        if self.sees_whole_register():
            return qs
        ids = organisation_ids(self.request.user)
        return qs.filter(
            Q(processor__organisation_id__in=ids)
            | Q(application__organisation_id__in=ids)
            | Q(application__created_by=self.request.user)
        ).distinct()

    @extend_schema(responses=ExpiringDocumentSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="expiring", url_name="expiring", pagination_class=None)
    def expiring(self, request):
        """Documents already expired or lapsing inside the warning window."""
        horizon = timezone.localdate() + timedelta(days=EXPIRY_WARNING_DAYS)
        documents = self.get_queryset().filter(expires_on__isnull=False, expires_on__lte=horizon).order_by("expires_on")
        return Response(ExpiringDocumentSerializer(documents, many=True).data)

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="download", url_name="download")
    def download(self, request, pk=None):
        document = self.get_object()
        if not document.file:
            raise AppError("No file has been uploaded for this document.", code="not_found", status_code=404)
        return serve_file(document.file, document.original_name)

    @extend_schema(request=ProcessingDocumentReviewSerializer, responses=ProcessingDocumentSerializer)
    @action(detail=True, methods=["post"], url_path="review", url_name="review")
    def review(self, request, pk=None):
        """Accept or reject one piece of evidence."""
        document = self.get_object()
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can review evidence.")
        if not document.file:
            raise ConflictError("This document has not been supplied yet.", code="document_not_supplied")
        serializer = ProcessingDocumentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document.review_state = serializer.validated_data["review_state"]
        document.review_note = serializer.validated_data.get("note", "")
        document.reviewed_by = request.user
        document.reviewed_at = timezone.now()
        document.save(update_fields=["review_state", "review_note", "reviewed_by", "reviewed_at", "updated_at"])
        self.record(
            "document_reviewed",
            target=f"{document.reference or document.name}",
            detail=f"{document.name} marked {document.get_review_state_display().lower()}.",
            application_id=str(document.application_id or ""),
            document_id=str(document.id),
        )
        return Response(ProcessingDocumentSerializer(document, context={"request": request}).data)


class ComplianceReportViewSet(ProcessingViewSetMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = ComplianceReportSerializer
    queryset = ComplianceReport.objects.all()
    permission_classes = [IsProcessingParticipant]
    filterset_fields = ["scope"]
    search_fields = ["title", "reference", "period_label"]
    ordering_fields = ["generated_on", "title"]
    ordering = ["-generated_on"]

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="download", url_name="download")
    def download(self, request, pk=None):
        report = self.get_object()
        if not report.file:
            raise AppError("This report has no stored file.", code="not_found", status_code=404)
        return serve_file(report.file, f"{report.reference}.pdf")


class ProcessingAuditViewSet(ProcessingViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """The processing trail. Operators and regulators only: it spans companies."""

    serializer_class = ProcessingAuditEventSerializer
    queryset = AccountAuditEvent.objects.none()
    ordering = ["-created_at"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        if not self.sees_whole_register():
            return AccountAuditEvent.objects.none()
        return AccountAuditEvent.objects.filter(event_type__startswith=audit.PREFIX).select_related("actor")


KPI_TREND_MONTHS = 6


def _month_key(value):
    return value.strftime("%Y-%m")


def _recent_months(count):
    """The first day of each of the last ``count`` months, oldest first.

    Stepping back to the previous month's last day and truncating is exact;
    subtracting a fixed number of days drifts and eventually skips a month.
    """
    month_start = timezone.localdate().replace(day=1)
    months = []
    for _ in range(count):
        months.append(month_start)
        month_start = (month_start - timedelta(days=1)).replace(day=1)
    return list(reversed(months))


class ProcessingDashboardView(APIView):
    """Everything the two processing dashboards read, in one round trip."""

    permission_classes = [IsProcessingParticipant]

    @extend_schema(responses=DashboardSerializer)
    def get(self, request):
        user = request.user
        role = audience(user)
        whole_register = role in {OPERATOR, REGULATOR}
        ids = organisation_ids(user)

        processors = Processor.objects.all()
        applications = ProcessingApplication.objects.all()
        findings = NonConformity.objects.all()
        inspections = Inspection.objects.all()
        alerts = EnvironmentalAlert.objects.all()
        incidents = Incident.objects.all()
        documents = ProcessingDocument.objects.all()

        if not whole_register:
            processors = processors.filter(organisation_id__in=ids)
            applications = applications.filter(
                Q(organisation_id__in=ids) | Q(processor__organisation_id__in=ids) | Q(created_by=user)
            )
            findings = findings.filter(Q(processor__organisation_id__in=ids) | Q(application__organisation_id__in=ids))
            inspections = inspections.filter(
                Q(processor__organisation_id__in=ids) | Q(application__organisation_id__in=ids)
            )
            alerts = alerts.filter(processor__organisation_id__in=ids)
            incidents = incidents.filter(processor__organisation_id__in=ids)
            documents = documents.filter(
                Q(processor__organisation_id__in=ids) | Q(application__organisation_id__in=ids)
            )

        today = timezone.localdate()
        horizon = today + timedelta(days=EXPIRY_WARNING_DAYS)
        open_findings = findings.exclude(status=NonConformity.Status.CLOSED)
        expiring = documents.filter(expires_on__isnull=False, expires_on__lte=horizon).order_by("expires_on")
        scores = list(processors.values_list("compliance_score", flat=True))

        totals = {
            "applications": applications.count(),
            "applications_in_review": applications.filter(stage=ApplicationStage.IN_REVIEW).count(),
            "applications_awaiting_info": applications.filter(stage=ApplicationStage.AWAITING_INFO).count(),
            "processors": processors.count(),
            "approved_processors": processors.filter(status=ProcessorStatus.APPROVED).count(),
            "suspended_processors": processors.filter(status=ProcessorStatus.SUSPENDED).count(),
            "open_non_conformities": open_findings.count(),
            "overdue_non_conformities": open_findings.filter(due_on__lt=today).count(),
            "upcoming_inspections": inspections.exclude(status=Inspection.Status.COMPLETED).count(),
            "open_environmental_alerts": alerts.exclude(status=EnvironmentalAlert.Status.RESOLVED).count(),
            "open_incidents": incidents.exclude(status=Incident.Status.CLOSED).count(),
            "expiring_documents": expiring.count(),
            "average_compliance_score": round(sum(scores) / len(scores)) if scores else 0,
        }

        payload = {
            "audience": role,
            "capabilities": capabilities(user),
            "totals": totals,
            "kpi_trend": self._kpi_trend(applications, findings, inspections),
            "regional_compliance": self._regional(processors),
            "expiring_documents": ExpiringDocumentSerializer(expiring[:20], many=True).data,
            "notifications": self._notifications(open_findings, alerts, expiring, inspections),
            "recent_applications": ProcessingApplicationSerializer(
                applications.select_related("processor").prefetch_related("risk_causes", "documents", "sections", "non_conformities")[:8],
                many=True,
                context={"request": request},
            ).data,
            "open_alerts": EnvironmentalAlertSerializer(
                alerts.exclude(status=EnvironmentalAlert.Status.RESOLVED)[:10], many=True
            ).data,
            "open_incidents": IncidentSerializer(incidents.exclude(status=Incident.Status.CLOSED)[:10], many=True).data,
        }
        return Response(payload)

    def _kpi_trend(self, applications, findings, inspections):
        """Six months of approvals, findings and inspections, oldest first."""
        buckets = OrderedDict(
            (_month_key(month), {"month": month.strftime("%b"), "approvals": 0, "nonconformities": 0, "inspections": 0})
            for month in _recent_months(KPI_TREND_MONTHS)
        )

        for decided_at in applications.filter(
            decision=ApplicationDecision.APPROVED, decided_at__isnull=False
        ).values_list("decided_at", flat=True):
            bucket = buckets.get(_month_key(timezone.localtime(decided_at).date()))
            if bucket:
                bucket["approvals"] += 1
        for raised_on in findings.values_list("raised_on", flat=True):
            bucket = buckets.get(_month_key(raised_on))
            if bucket:
                bucket["nonconformities"] += 1
        for scheduled_for in inspections.filter(scheduled_for__isnull=False).values_list("scheduled_for", flat=True):
            bucket = buckets.get(_month_key(scheduled_for))
            if bucket:
                bucket["inspections"] += 1

        return list(buckets.values())

    def _regional(self, processors):
        rows = {}
        for processor in processors.only("region", "state", "status", "compliance_score"):
            region = processor.region or processor.state or "Unassigned"
            row = rows.setdefault(
                region,
                {"region": region, "processors": 0, "compliant": 0, "conditional": 0, "suspended": 0, "_score": 0},
            )
            row["processors"] += 1
            row["_score"] += processor.compliance_score
            if processor.status == ProcessorStatus.APPROVED:
                row["compliant"] += 1
            elif processor.status == ProcessorStatus.CONDITIONAL:
                row["conditional"] += 1
            elif processor.status == ProcessorStatus.SUSPENDED:
                row["suspended"] += 1
        result = []
        for row in rows.values():
            count = row.pop("_score")
            row["avg_score"] = round(count / row["processors"]) if row["processors"] else 0
            result.append(row)
        return sorted(result, key=lambda r: -r["processors"])

    def _notifications(self, open_findings, alerts, expiring, inspections):
        """The handful of items the shell's bell should surface, newest first."""
        items = []
        for finding in open_findings.filter(status=NonConformity.Status.EVIDENCE_SUBMITTED)[:3]:
            items.append({
                "id": f"nc-{finding.id}",
                "title": f"{finding.reference} evidence submitted",
                "body": f"{finding.title} is awaiting review.",
                "at": finding.updated_at,
                "kind": "info",
            })
        for alert in alerts.filter(status=EnvironmentalAlert.Status.OPEN, severity=EnvironmentalAlert.Severity.CRITICAL)[:3]:
            items.append({
                "id": f"env-{alert.id}",
                "title": "Critical environmental alert",
                "body": f"{alert.parameter} at {alert.facility_name or 'a registered facility'} read {alert.reading}.",
                "at": alert.updated_at,
                "kind": "error",
            })
        for document in expiring[:3]:
            days = document.days_to_expiry
            items.append({
                "id": f"doc-{document.id}",
                "title": "Document expiring",
                "body": f"{document.name} {'expired' if days is not None and days < 0 else 'expires'} on {document.expires_on:%d %b %Y}.",
                "at": document.updated_at,
                "kind": "warn",
            })
        for inspection in inspections.filter(status=Inspection.Status.SCHEDULED)[:2]:
            items.append({
                "id": f"ins-{inspection.id}",
                "title": "Inspection scheduled",
                "body": f"{inspection.reference} at {inspection.facility_name or 'a registered facility'}.",
                "at": inspection.updated_at,
                "kind": "info",
            })
        return sorted(items, key=lambda item: item["at"], reverse=True)[:8]


class ProcessingChecklistView(APIView):
    """What an application of a given process class must answer and evidence.

    Served rather than duplicated in the client: completeness is computed from
    this, so the definition of "complete" cannot be a client's opinion of it.
    """

    permission_classes = [IsProcessingParticipant]

    @extend_schema(
        parameters=[OpenApiParameter("processing_type", OpenApiTypes.STR, OpenApiParameter.QUERY)],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        processing_type = request.query_params.get("processing_type", "")
        valid = {value for value, _ in ProcessingType.choices}
        if processing_type and processing_type not in valid:
            raise AppError("Unknown processing type.", code="unknown_processing_type")
        return Response({
            "processing_type": processing_type,
            "sections": checklist.checklist(processing_type),
        })


class ProcessingCapabilityView(APIView):
    """What this caller may do in the processing vertical.

    The frontend keeps a chosen dashboard role in localStorage; this is the
    authoritative answer, so the UI can render against real permissions rather
    than against a stored preference.
    """

    permission_classes = [IsProcessingParticipant]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(capabilities(request.user))
