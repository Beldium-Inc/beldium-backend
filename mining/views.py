"""API for the Mining Compliance register.

Read scope is decided in ``get_queryset`` from the caller's audience: the
operator desk and the regulator see the whole register, a mining company
sees only its own sites' records. Write scope is decided by the permission
classes. Neither reads a role from the request, so a client cannot widen its
own access. Mirrors ``processing.views`` closely — see ``mining.models`` for
why the two verticals stay separate apps.
"""
from collections import OrderedDict
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_account_event
from accounts.models import AccountAuditEvent
from organisations.models import Organisation
from common.exceptions import AppError, ConflictError
from mining import audit, checklist, reports, scoring, verification
from mining.models import (
    Application,
    CorrectiveSubmission,
    DocumentRecord,
    Equipment,
    EnvRecord,
    Evidence,
    InfoRequest,
    Inspection,
    InventoryItem,
    LicenceDoc,
    MineSite,
    MiningOrganisationProfile,
    NonConformity,
    PendingReview,
    ProductionRecord,
    ReviewSection,
    Sample,
    SafetyIncident,
    ScoreFactor,
    SectionKey,
    SiteStatus,
)
from mining.permissions import (
    MINER,
    OPERATOR,
    REGULATOR,
    IsMiningOperator,
    IsMiningParticipant,
    audience,
    can_decide,
    capabilities,
    is_operator,
    organisation_ids,
    owns_site,
)
from mining.serializers import (
    ApplicationSerializer,
    CorrectiveSubmissionSerializer,
    DashboardSerializer,
    DocumentRecordSerializer,
    EnvRecordSerializer,
    EquipmentSerializer,
    EvidenceSerializer,
    ExpiringLicenceSerializer,
    InfoRequestResponseSerializer,
    InfoRequestSerializer,
    InspectionRequestSerializer,
    InspectionSerializer,
    InventoryItemSerializer,
    LicenceDocSerializer,
    MineSiteDetailSerializer,
    MineSiteSerializer,
    MiningAuditEventSerializer,
    MiningOrganisationProfileSerializer,
    MiningSectionReviewSerializer,
    NonConformityClosureSerializer,
    NonConformitySerializer,
    PendingReviewSerializer,
    ProductionRecordSerializer,
    ReviewSectionSerializer,
    SafetyIncidentSerializer,
    SampleSerializer,
    ScoreFactorSerializer,
)

LICENCE_EXPIRY_WARNING_DAYS = 60
KPI_TREND_MONTHS = 6


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


class MiningViewSetMixin:
    """Shared scoping, auditing and transaction handling."""

    permission_classes = [IsMiningOperator]

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
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


class MiningOrganisationProfileViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = MiningOrganisationProfileSerializer
    queryset = MiningOrganisationProfile.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["organisation"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = MiningOrganisationProfile.objects.select_related("organisation")
        if self.sees_whole_register():
            return qs
        return qs.filter(organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not is_operator(self.request.user) and serializer.validated_data.get("organisation").id not in organisation_ids(self.request.user):
            raise PermissionDenied("You cannot create a profile for another organisation.")
        profile = serializer.save()
        self.record("organisation_profile_created", target=profile.organisation.name)

    def perform_update(self, serializer):
        profile = serializer.save()
        self.record("organisation_profile_updated", target=profile.organisation.name)


class MineSiteViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    """The register of mine sites."""

    serializer_class = MineSiteSerializer
    queryset = MineSite.objects.none()
    permission_classes = [IsMiningParticipant]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filterset_fields = ["status", "mineral", "state", "risk", "organisation"]
    search_fields = ["name", "code", "mineral", "state"]
    ordering_fields = ["name", "compliance_score", "last_inspection_on"]
    ordering = ["name"]

    def get_serializer_class(self):
        return MineSiteDetailSerializer if self.action == "retrieve" else MineSiteSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = MineSite.objects.select_related("organisation").annotate(
            open_non_conformities_count=Count(
                "non_conformities", filter=~Q(non_conformities__status=NonConformity.Status.CLOSED), distinct=True
            )
        )
        qs = qs.order_by("name")
        if self.action == "retrieve":
            qs = qs.prefetch_related("sections__evidence", "score_factors")
        if self.sees_whole_register():
            return qs
        return qs.filter(organisation_id__in=organisation_ids(self.request.user))

    def get_object(self):
        site = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            return MineSite.objects.select_for_update().select_related("organisation").get(pk=site.pk)
        return site

    def _assert_owns(self, site):
        if not owns_site(self.request.user, site):
            raise PermissionDenied("You cannot act on another organisation's site.")

    def perform_create(self, serializer):
        if not is_operator(self.request.user):
            allowed = set(organisation_ids(self.request.user))
            organisation_id = getattr(serializer.validated_data.get("organisation"), "id", None)
            if organisation_id and organisation_id not in allowed:
                raise PermissionDenied("You cannot register a site for another organisation.")
        site = serializer.save()
        ReviewSection.objects.bulk_create(
            [ReviewSection(site=site, key=key) for key, _ in SectionKey.choices]
        )
        self.record("site_registered", target=site.name, detail=f"Registered on {site.mineral}.", site_id=str(site.id))

    def perform_update(self, serializer):
        self._assert_owns(serializer.instance)
        site = serializer.save()
        self.record("site_updated", target=site.name, detail=f"Status {site.get_status_display()}, score {site.compliance_score}.", site_id=str(site.id))

    @extend_schema(
        request=ReviewSectionSerializer,
        responses=ReviewSectionSerializer,
        parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["patch"], url_path=r"sections/(?P<key>[a-z_]+)", url_name="section")
    def section(self, request, pk=None, key=None):
        """Site-side save of one evidence section."""
        site = self.get_object()
        self._assert_owns(site)
        section = site.sections.filter(key=key).first()
        if not section:
            raise AppError("Unknown site section.", code="unknown_section", status_code=404)
        serializer = ReviewSectionSerializer(section, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save(status="pending", decision_note="", decided_by=None, decided_at=None)
        self.record("section_saved", target=f"{site.name} · {SectionKey(key).label}", site_id=str(site.id), section=key)
        return Response(ReviewSectionSerializer(section, context={"request": request}).data)

    @extend_schema(
        request=MiningSectionReviewSerializer,
        responses=ReviewSectionSerializer,
        parameters=[OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["post"], url_path=r"sections/(?P<key>[a-z_]+)/review", url_name="review-section")
    def review_section(self, request, pk=None, key=None):
        """Operator-side verdict on one evidence section."""
        site = self.get_object()
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can review a section.")
        section = site.sections.filter(key=key).first()
        if not section:
            raise AppError("Unknown site section.", code="unknown_section", status_code=404)
        serializer = MiningSectionReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section.status = serializer.validated_data["status"]
        section.decision_note = serializer.validated_data.get("note", "")
        if "score" in serializer.validated_data:
            section.score = serializer.validated_data["score"]
        elif section.status == "verified":
            section.score = 100
        elif section.status in {"rejected", "flagged"}:
            section.score = 0
        section.decided_by = request.user
        section.decided_at = timezone.now()
        section.save(update_fields=["status", "decision_note", "score", "decided_by", "decided_at", "updated_at"])
        self.record(
            "section_reviewed", target=f"{site.name} · {SectionKey(key).label}",
            detail=f"Marked {section.get_status_display().lower()}.", site_id=str(site.id), section=key, status=section.status,
        )
        scoring.apply_review_outcome(site)
        return Response(ReviewSectionSerializer(section, context={"request": request}).data)

    @extend_schema(methods=["GET"], responses=EvidenceSerializer(many=True))
    @extend_schema(methods=["POST"], request=EvidenceSerializer, responses={201: EvidenceSerializer})
    @action(detail=True, methods=["get", "post"], url_path=r"sections/(?P<key>[a-z_]+)/evidence",
            url_name="section-evidence", parser_classes=[JSONParser, MultiPartParser, FormParser], pagination_class=None)
    def section_evidence(self, request, pk=None, key=None):
        site = self.get_object()
        section = site.sections.filter(key=key).first()
        if not section:
            raise AppError("Unknown site section.", code="unknown_section", status_code=404)
        if request.method == "GET":
            return Response(EvidenceSerializer(section.evidence.all(), many=True, context={"request": request}).data)
        self._assert_owns(site)
        serializer = EvidenceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        evidence = serializer.save(section=section, uploaded_by=request.user)
        self.record("evidence_uploaded", target=f"{site.name} · {evidence.name}", site_id=str(site.id), section=key, evidence_id=str(evidence.id))
        return Response(EvidenceSerializer(evidence, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path=r"evidence/(?P<evidence_id>[^/.]+)/download", url_name="download-evidence")
    def download_evidence(self, request, pk=None, evidence_id=None):
        site = self.get_object()
        evidence = Evidence.objects.filter(id=evidence_id, section__site=site).first()
        if not evidence or not evidence.file:
            raise AppError("Evidence file not found.", code="not_found", status_code=404)
        return serve_file(evidence.file, evidence.original_name)

    @extend_schema(methods=["GET"], responses=ScoreFactorSerializer(many=True))
    @extend_schema(methods=["POST"], request=ScoreFactorSerializer, responses={201: ScoreFactorSerializer})
    @action(detail=True, methods=["get", "post"], url_path="score-factors", url_name="score-factors", pagination_class=None)
    def score_factors(self, request, pk=None):
        site = self.get_object()
        if request.method == "GET":
            return Response(ScoreFactorSerializer(site.score_factors.all(), many=True).data)
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can add a score factor.")
        serializer = ScoreFactorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        factor = serializer.save(site=site)
        self.record("score_factor_added", target=site.name, detail=f"{factor.label} ({factor.score}/100, weight {factor.weight}).", site_id=str(site.id))
        return Response(ScoreFactorSerializer(factor).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=MineSiteDetailSerializer)
    @action(detail=True, methods=["post"], url_path="recompute-score", url_name="recompute-score")
    def recompute_score(self, request, pk=None):
        """Set the stored score from the weighted section/factor average."""
        site = self.get_object()
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can recompute the score.")
        site.compliance_score = scoring.weighted_score(site)
        site.save(update_fields=["compliance_score", "updated_at"])
        self.record("score_recomputed", target=site.name, detail=f"Score set to {site.compliance_score}.", site_id=str(site.id))
        return Response(MineSiteDetailSerializer(site, context={"request": request}).data)

    @extend_schema(responses=MiningAuditEventSerializer(many=True))
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        site = self.get_object()
        events = AccountAuditEvent.objects.filter(
            event_type__startswith=audit.PREFIX, metadata__site_id=str(site.id)
        ).select_related("actor")
        page = self.paginate_queryset(events)
        serializer = MiningAuditEventSerializer(page if page is not None else events, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class NonConformityViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    """Findings raised against a mine site."""

    serializer_class = NonConformitySerializer
    queryset = NonConformity.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "severity", "site"]
    search_fields = ["reference", "title"]
    ordering_fields = ["deadline", "severity", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = NonConformity.objects.select_related("site").prefetch_related("submissions")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can raise a finding.")
        finding = serializer.save(raised_by=self.request.user)
        self.record("non_conformity_raised", target=finding.reference, detail=f"{finding.get_severity_display()} finding: {finding.title}", site_id=str(finding.site_id), non_conformity_id=str(finding.id))

    def perform_update(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can amend a finding.")
        finding = serializer.save()
        self.record("non_conformity_updated", target=finding.reference, detail=finding.title, non_conformity_id=str(finding.id))

    @extend_schema(request=CorrectiveSubmissionSerializer, responses={201: CorrectiveSubmissionSerializer})
    @action(detail=True, methods=["post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def submissions(self, request, pk=None):
        """Corrective-action evidence, submitted by the site operator."""
        finding = self.get_object()
        if finding.status == NonConformity.Status.CLOSED:
            raise ConflictError("This finding is already closed.", code="non_conformity_closed")
        if not owns_site(request.user, finding.site):
            raise PermissionDenied("You can only submit corrective action for your own site.")
        serializer = CorrectiveSubmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission = serializer.save(non_conformity=finding, submitted_by=request.user)
        finding.status = NonConformity.Status.AWAITING_REVIEW
        finding.save(update_fields=["status", "updated_at"])
        self.record("corrective_submission_added", target=finding.reference, non_conformity_id=str(finding.id))
        return Response(CorrectiveSubmissionSerializer(submission).data, status=status.HTTP_201_CREATED)

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
        if accept and not finding.submissions.exists():
            raise ConflictError("No corrective-action evidence has been submitted.", code="evidence_required")
        finding.status = NonConformity.Status.CLOSED if accept else NonConformity.Status.OPEN
        finding.save(update_fields=["status", "updated_at"])
        note = serializer.validated_data.get("note", "")
        submission = finding.submissions.order_by("-created_at").first()
        if submission:
            submission.decision = CorrectiveSubmission.Decision.ACCEPTED if accept else CorrectiveSubmission.Decision.REJECTED
            submission.decision_note = note
            submission.decided_by = request.user
            submission.decided_at = timezone.now()
            submission.save(update_fields=["decision", "decision_note", "decided_by", "decided_at", "updated_at"])
        self.record(
            "non_conformity_closed" if accept else "non_conformity_reopened",
            target=finding.reference, detail=note or ("Evidence accepted." if accept else "Evidence rejected; finding reopened."),
            non_conformity_id=str(finding.id),
        )
        return Response(NonConformitySerializer(finding, context={"request": request}).data)


class InspectionViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = InspectionSerializer
    queryset = Inspection.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "type", "site"]
    search_fields = ["reference", "inspector_name"]
    ordering_fields = ["scheduled_for", "created_at"]
    ordering = ["-scheduled_for", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Inspection.objects.select_related("site", "inspector")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def _assert_desk(self):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can schedule inspections.")

    def perform_create(self, serializer):
        self._assert_desk()
        inspection = serializer.save()
        self.record("inspection_scheduled", target=inspection.reference, detail=f"{inspection.get_type_display()} at {inspection.site.name if inspection.site_id else 'site'}.", inspection_id=str(inspection.id), site_id=str(inspection.site_id or ""))

    def perform_update(self, serializer):
        self._assert_desk()
        inspection = serializer.save()
        if inspection.status == Inspection.Status.COMPLETED and not inspection.completed_at:
            inspection.completed_at = timezone.now()
            inspection.save(update_fields=["completed_at", "updated_at"])
            if inspection.site_id:
                inspection.site.last_inspection_on = timezone.localdate()
                inspection.site.save(update_fields=["last_inspection_on", "updated_at"])
        self.record("inspection_updated", target=inspection.reference, detail=inspection.result or inspection.get_status_display(), inspection_id=str(inspection.id), site_id=str(inspection.site_id or ""))


class SampleViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = SampleSerializer
    queryset = Sample.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "site"]
    search_fields = ["reference", "lab", "certificate"]
    ordering_fields = ["collected_on"]
    ordering = ["-collected_on"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Sample.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only record samples for your own sites.")
        sample = serializer.save()
        self.record("sample_recorded", target=sample.reference, site_id=str(sample.site_id or ""))

    def perform_update(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can amend a sample record.")
        sample = serializer.save()
        self.record("sample_updated", target=sample.reference, site_id=str(sample.site_id or ""))


class EnvRecordViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EnvRecordSerializer
    queryset = EnvRecord.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "site"]
    search_fields = ["metric"]
    ordering_fields = ["measured_on"]
    ordering = ["-measured_on"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = EnvRecord.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        record = serializer.save()
        self.record("env_record_added", target=f"{record.metric}", detail=f"{record.value} against {record.limit}.", site_id=str(record.site_id or ""))

    def perform_update(self, serializer):
        record = serializer.save()
        self.record("env_record_updated", target=record.metric, site_id=str(record.site_id or ""))


class SafetyIncidentViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = SafetyIncidentSerializer
    queryset = SafetyIncident.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "severity", "site"]
    search_fields = ["type"]
    ordering_fields = ["date"]
    ordering = ["-date"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = SafetyIncident.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only report incidents at your own sites.")
        incident = serializer.save()
        self.record("safety_incident_reported", target=incident.type, detail=incident.get_severity_display(), site_id=str(incident.site_id or ""))

    def perform_update(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can update an incident record.")
        incident = serializer.save()
        self.record("safety_incident_updated", target=incident.type, detail=incident.get_status_display(), site_id=str(incident.site_id or ""))


class EquipmentViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EquipmentSerializer
    queryset = Equipment.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["status", "site"]
    search_fields = ["name", "serial"]
    ordering_fields = ["name", "cert_expires_on"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Equipment.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only register equipment at your own sites.")
        equipment = serializer.save()
        self.record("equipment_registered", target=equipment.name, site_id=str(equipment.site_id or ""))

    def perform_update(self, serializer):
        if not owns_site(self.request.user, serializer.instance.site):
            raise PermissionDenied("You can only update equipment at your own sites.")
        equipment = serializer.save()
        self.record("equipment_updated", target=equipment.name, site_id=str(equipment.site_id or ""))


class ProductionRecordViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    """Site-level production reporting, the queryable counterpart of MineSite.production."""

    serializer_class = ProductionRecordSerializer
    queryset = ProductionRecord.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["site", "commodity"]
    search_fields = ["commodity", "notes"]
    ordering_fields = ["period_start", "period_end", "tonnage"]
    ordering = ["-period_start"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = ProductionRecord.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only record production at your own sites.")
        record = serializer.save()
        self.record("production_record_created", target=record.commodity, site_id=str(record.site_id or ""))

    def perform_update(self, serializer):
        if not owns_site(self.request.user, serializer.instance.site):
            raise PermissionDenied("You can only update production records at your own sites.")
        record = serializer.save()
        self.record("production_record_updated", target=record.commodity, site_id=str(record.site_id or ""))


class InventoryItemViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    """Site-level inventory, the queryable counterpart of MineSite.inventory."""

    serializer_class = InventoryItemSerializer
    queryset = InventoryItem.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filterset_fields = ["site", "category"]
    search_fields = ["name", "category"]
    ordering_fields = ["name", "quantity"]
    ordering = ["name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = InventoryItem.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only record inventory at your own sites.")
        item = serializer.save()
        self.record("inventory_item_created", target=item.name, site_id=str(item.site_id or ""))

    def perform_update(self, serializer):
        if not owns_site(self.request.user, serializer.instance.site):
            raise PermissionDenied("You can only update inventory at your own sites.")
        item = serializer.save()
        self.record("inventory_item_updated", target=item.name, site_id=str(item.site_id or ""))


class ApplicationViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ApplicationSerializer
    queryset = Application.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "organisation", "site"]
    search_fields = ["reference", "site_name"]
    ordering_fields = ["submitted_on", "created_at"]
    ordering = ["-submitted_on", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = Application.objects.select_related("site", "organisation")
        if self.sees_whole_register():
            return qs
        ids = organisation_ids(self.request.user)
        return qs.filter(
            Q(organisation_id__in=ids) | Q(site__organisation_id__in=ids) | Q(created_by=self.request.user)
        ).distinct()

    def perform_create(self, serializer):
        if not is_operator(self.request.user):
            allowed = set(organisation_ids(self.request.user))
            organisation_id = getattr(serializer.validated_data.get("organisation"), "id", None)
            if organisation_id and organisation_id not in allowed:
                raise PermissionDenied("You cannot file an application for another organisation.")
        application = serializer.save(created_by=self.request.user)
        self.record("application_created", target=application.reference, detail=application.site_name, application_id=str(application.id))

    def perform_update(self, serializer):
        if not can_decide(self.request.user) and serializer.instance.created_by_id != self.request.user.id:
            raise PermissionDenied("You cannot edit this application.")
        application = serializer.save()
        self.record("application_updated", target=application.reference, application_id=str(application.id))


class PendingReviewViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = PendingReviewSerializer
    queryset = PendingReview.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "priority", "site"]
    search_fields = ["subject"]
    ordering_fields = ["due_on", "created_at"]
    ordering = ["due_on", "-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = PendingReview.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can queue a review.")
        review = serializer.save()
        self.record("pending_review_added", target=review.subject, site_id=str(review.site_id or ""))

    def perform_update(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can update a queued review.")
        review = serializer.save()
        self.record("pending_review_updated", target=review.subject, site_id=str(review.site_id or ""))


class InfoRequestViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = InfoRequestSerializer
    queryset = InfoRequest.objects.none()
    permission_classes = [IsMiningParticipant]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "priority", "site"]
    search_fields = ["subject"]
    ordering_fields = ["due_by", "created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = InfoRequest.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not can_decide(self.request.user):
            raise PermissionDenied("Only the compliance operator desk can raise an information request.")
        info_request = serializer.save(requested_by=self.request.user)
        self.record("info_request_raised", target=info_request.subject, site_id=str(info_request.site_id or ""))

    @extend_schema(request=InfoRequestResponseSerializer, responses=InfoRequestSerializer)
    @action(detail=True, methods=["post"])
    def respond(self, request, pk=None):
        info_request = self.get_object()
        if not owns_site(request.user, info_request.site):
            raise PermissionDenied("You can only respond on behalf of your own site.")
        if info_request.status == InfoRequest.Status.CLOSED:
            raise ConflictError("This information request is already closed.", code="info_request_closed")
        serializer = InfoRequestResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        info_request.response_message = serializer.validated_data["message"]
        info_request.response_by = request.user
        info_request.response_at = timezone.now()
        info_request.status = InfoRequest.Status.RESPONDED
        info_request.save(update_fields=["response_message", "response_by", "response_at", "status", "updated_at"])
        self.record("info_request_responded", target=info_request.subject, site_id=str(info_request.site_id or ""))
        return Response(InfoRequestSerializer(info_request).data)


class LicenceDocViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = LicenceDocSerializer
    queryset = LicenceDoc.objects.none()
    permission_classes = [IsMiningParticipant]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = ["status", "site"]
    search_fields = ["number", "type", "authority"]
    ordering_fields = ["expires_on", "issued_on"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = LicenceDoc.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only add licences for your own sites.")
        licence = serializer.save()
        self.record("licence_added", target=f"{licence.type} · {licence.number}", site_id=str(licence.site_id or ""))

    def perform_update(self, serializer):
        if not owns_site(self.request.user, serializer.instance.site):
            raise PermissionDenied("You can only update licences for your own sites.")
        licence = serializer.save()
        self.record("licence_updated", target=f"{licence.type} · {licence.number}", site_id=str(licence.site_id or ""))

    @extend_schema(responses=ExpiringLicenceSerializer(many=True))
    @action(detail=False, methods=["get"], url_path="expiring", url_name="expiring", pagination_class=None)
    def expiring(self, request):
        horizon = timezone.localdate() + timedelta(days=LICENCE_EXPIRY_WARNING_DAYS)
        licences = self.get_queryset().filter(expires_on__isnull=False, expires_on__lte=horizon).order_by("expires_on")
        return Response(ExpiringLicenceSerializer(licences, many=True).data)

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="download", url_name="download")
    def download(self, request, pk=None):
        licence = self.get_object()
        if not licence.file:
            raise AppError("No file has been uploaded for this licence.", code="not_found", status_code=404)
        return serve_file(licence.file, f"{licence.type}-{licence.number}")


class DocumentRecordViewSet(MiningViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DocumentRecordSerializer
    queryset = DocumentRecord.objects.none()
    permission_classes = [IsMiningParticipant]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filterset_fields = ["status", "site", "category"]
    search_fields = ["name", "category"]
    ordering_fields = ["expires_on", "name"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = DocumentRecord.objects.select_related("site")
        if self.sees_whole_register():
            return qs
        return qs.filter(site__organisation_id__in=organisation_ids(self.request.user))

    def perform_create(self, serializer):
        if not owns_site(self.request.user, serializer.validated_data.get("site")):
            raise PermissionDenied("You can only add documents for your own sites.")
        document = serializer.save(uploaded_by=self.request.user)
        self.record("document_uploaded", target=document.name, site_id=str(document.site_id or ""))

    @extend_schema(request=None, responses=DocumentRecordSerializer)
    @action(detail=True, methods=["post"], url_path="review", url_name="review")
    def review(self, request, pk=None):
        document = self.get_object()
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can review a document.")
        if not document.file:
            raise ConflictError("This document has not been supplied yet.", code="document_not_supplied")
        review_status = request.data.get("status")
        if review_status not in {"verified", "rejected"}:
            raise AppError("status must be verified or rejected.", code="invalid_status")
        document.status = review_status
        document.save(update_fields=["status", "updated_at"])
        self.record("document_reviewed", target=document.name, detail=f"Marked {document.get_status_display().lower()}.", document_id=str(document.id), site_id=str(document.site_id or ""))
        return Response(DocumentRecordSerializer(document, context={"request": request}).data)

    @extend_schema(responses={200: OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path="download", url_name="download")
    def download(self, request, pk=None):
        document = self.get_object()
        if not document.file:
            raise AppError("No file has been uploaded for this document.", code="not_found", status_code=404)
        return serve_file(document.file, document.original_name)


class MiningAuditViewSet(MiningViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """The mining trail. Operators and regulators only: it spans companies."""

    serializer_class = MiningAuditEventSerializer
    queryset = AccountAuditEvent.objects.none()
    ordering = ["-created_at"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        if not self.sees_whole_register():
            return AccountAuditEvent.objects.none()
        return AccountAuditEvent.objects.filter(event_type__startswith=audit.PREFIX).select_related("actor")


def _month_key(value):
    return value.strftime("%Y-%m")


def _recent_months(count):
    """The first day of each of the last ``count`` months, oldest first."""
    month_start = timezone.localdate().replace(day=1)
    months = []
    for _ in range(count):
        months.append(month_start)
        month_start = (month_start - timedelta(days=1)).replace(day=1)
    return list(reversed(months))


class MiningDashboardView(APIView):
    """Everything the mining compliance dashboard reads, in one round trip."""

    permission_classes = [IsMiningParticipant]

    @extend_schema(responses=DashboardSerializer)
    def get(self, request):
        user = request.user
        role = audience(user)
        whole_register = role in {OPERATOR, REGULATOR}
        ids = organisation_ids(user)

        sites = MineSite.objects.all()
        applications = Application.objects.all()
        findings = NonConformity.objects.all()
        inspections = Inspection.objects.all()
        env_records = EnvRecord.objects.all()
        safety_incidents = SafetyIncident.objects.all()
        licences = LicenceDoc.objects.all()

        if not whole_register:
            sites = sites.filter(organisation_id__in=ids)
            applications = applications.filter(Q(organisation_id__in=ids) | Q(site__organisation_id__in=ids) | Q(created_by=user))
            findings = findings.filter(site__organisation_id__in=ids)
            inspections = inspections.filter(site__organisation_id__in=ids)
            env_records = env_records.filter(site__organisation_id__in=ids)
            safety_incidents = safety_incidents.filter(site__organisation_id__in=ids)
            licences = licences.filter(site__organisation_id__in=ids)

        today = timezone.localdate()
        horizon = today + timedelta(days=LICENCE_EXPIRY_WARNING_DAYS)
        open_findings = findings.exclude(status=NonConformity.Status.CLOSED)
        expiring = licences.filter(expires_on__isnull=False, expires_on__lte=horizon).select_related("site").order_by("expires_on")
        scores = list(sites.values_list("compliance_score", flat=True))

        totals = {
            "sites": sites.count(),
            "operational_sites": sites.filter(status=SiteStatus.OPERATIONAL).count(),
            "suspended_sites": sites.filter(status=SiteStatus.SUSPENDED).count(),
            "applications": applications.count(),
            "applications_pending": applications.filter(status=Application.Status.PENDING).count(),
            "applications_under_review": applications.filter(status=Application.Status.UNDER_REVIEW).count(),
            "open_non_conformities": open_findings.count(),
            "overdue_non_conformities": open_findings.filter(deadline__lt=today).count(),
            "upcoming_inspections": inspections.exclude(status=Inspection.Status.COMPLETED).count(),
            "environmental_watch": env_records.filter(status=EnvRecord.Status.WATCH).count(),
            "environmental_breaches": env_records.filter(status=EnvRecord.Status.BREACH).count(),
            "open_safety_incidents": safety_incidents.filter(status=SafetyIncident.Status.INVESTIGATING).count(),
            "expiring_licences": expiring.count(),
            "average_compliance_score": round(sum(scores) / len(scores)) if scores else 0,
        }

        payload = {
            "audience": role,
            "capabilities": capabilities(user),
            "totals": totals,
            "kpi_trend": self._kpi_trend(applications, findings, inspections),
            "regional_compliance": self._regional(sites),
            "expiring_licences": ExpiringLicenceSerializer(expiring[:20], many=True).data,
            "notifications": self._notifications(open_findings, env_records, expiring, inspections),
            "recent_applications": ApplicationSerializer(
                applications.select_related("site")[:8], many=True
            ).data,
            "open_environmental_records": EnvRecordSerializer(
                env_records.exclude(status=EnvRecord.Status.WITHIN_LIMIT)[:10], many=True
            ).data,
            "open_incidents": SafetyIncidentSerializer(
                safety_incidents.filter(status=SafetyIncident.Status.INVESTIGATING)[:10], many=True
            ).data,
        }
        return Response(payload)

    def _kpi_trend(self, applications, findings, inspections):
        """Six months of approvals, findings and inspections, oldest first."""
        buckets = OrderedDict(
            (_month_key(month), {"month": month.strftime("%b"), "approvals": 0, "nonconformities": 0, "inspections": 0})
            for month in _recent_months(KPI_TREND_MONTHS)
        )

        for updated_at in applications.filter(status=Application.Status.APPROVED).values_list("updated_at", flat=True):
            bucket = buckets.get(_month_key(timezone.localtime(updated_at).date()))
            if bucket:
                bucket["approvals"] += 1
        for created_at in findings.values_list("created_at", flat=True):
            bucket = buckets.get(_month_key(timezone.localtime(created_at).date()))
            if bucket:
                bucket["nonconformities"] += 1
        for scheduled_for in inspections.filter(scheduled_for__isnull=False).values_list("scheduled_for", flat=True):
            bucket = buckets.get(_month_key(scheduled_for))
            if bucket:
                bucket["inspections"] += 1

        return list(buckets.values())

    def _regional(self, sites):
        rows = {}
        for site in sites.only("state", "status", "compliance_score"):
            region = site.state or "Unassigned"
            row = rows.setdefault(
                region,
                {"region": region, "sites": 0, "operational": 0, "under_review": 0, "suspended": 0, "_score": 0},
            )
            row["sites"] += 1
            row["_score"] += site.compliance_score
            if site.status == SiteStatus.OPERATIONAL:
                row["operational"] += 1
            elif site.status == SiteStatus.UNDER_REVIEW:
                row["under_review"] += 1
            elif site.status == SiteStatus.SUSPENDED:
                row["suspended"] += 1
        result = []
        for row in rows.values():
            count = row.pop("_score")
            row["avg_score"] = round(count / row["sites"]) if row["sites"] else 0
            result.append(row)
        return sorted(result, key=lambda r: -r["sites"])

    def _notifications(self, open_findings, env_records, expiring, inspections):
        """The handful of items the shell's bell should surface, newest first."""
        items = []
        for finding in open_findings.filter(status=NonConformity.Status.AWAITING_REVIEW)[:3]:
            items.append({
                "id": f"nc-{finding.id}",
                "title": f"{finding.reference} awaiting review",
                "body": f"{finding.title} is awaiting review.",
                "at": finding.updated_at,
                "kind": "info",
                "entity": "non_conformity",
                "entity_id": str(finding.id),
                "reference": finding.reference,
            })
        for record in env_records.filter(status=EnvRecord.Status.BREACH)[:3]:
            items.append({
                "id": f"env-{record.id}",
                "title": "Environmental limit breached",
                "body": f"{record.metric} at {record.site.name if record.site_id else 'a registered site'} read {record.value} against a {record.limit} limit.",
                "at": record.updated_at,
                "kind": "error",
                "entity": "env_record",
                "entity_id": str(record.id),
                "reference": "",
            })
        for licence in expiring[:3]:
            days = (licence.expires_on - timezone.localdate()).days if licence.expires_on else None
            holder = licence.site.name if licence.site_id else ""
            items.append({
                "id": f"lic-{licence.id}",
                "title": f"{licence.type} {'expired' if days is not None and days < 0 else 'expiring'}",
                "body": (
                    f"{holder + ': ' if holder else ''}"
                    f"{'expired' if days is not None and days < 0 else 'expires'} on {licence.expires_on:%d %b %Y}."
                ),
                "at": licence.updated_at,
                "kind": "warn",
                "entity": "licence",
                "entity_id": str(licence.id),
                "reference": licence.number,
            })
        for inspection in inspections.filter(status=Inspection.Status.SCHEDULED)[:2]:
            items.append({
                "id": f"ins-{inspection.id}",
                "title": "Inspection scheduled",
                "body": (
                    f"{inspection.get_type_display()} at "
                    f"{inspection.site.name if inspection.site_id else 'a registered site'}"
                    + (f" on {inspection.scheduled_for:%d %b %Y}" if inspection.scheduled_for else "")
                    + "."
                ),
                "at": inspection.updated_at,
                "kind": "info",
                "entity": "inspection",
                "entity_id": str(inspection.id),
                "reference": inspection.reference,
            })
        return sorted(items, key=lambda item: item["at"], reverse=True)[:8]


class MiningCapabilityView(APIView):
    """What this caller may do in the mining compliance vertical."""

    permission_classes = [IsMiningParticipant]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(capabilities(request.user))


class MiningChecklistView(APIView):
    """What a mine site's review must answer and evidence, section by section.

    Served rather than duplicated in the client: completeness is computed
    from this, so the definition of "complete" cannot be a client's opinion
    of it.
    """

    permission_classes = [IsMiningParticipant]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"sections": checklist.checklist()})


class MiningReportView(APIView):
    """Compile and stream one oversight report from the register.

    Unlike ``processing.views.ComplianceReportViewSet``, nothing is persisted:
    the mining app has no report-storage model (see ``mining.reports`` for
    why), so this hands the PDF back directly rather than saving and offering
    a separate download route.
    """

    permission_classes = [IsMiningParticipant]

    @extend_schema(
        parameters=[
            OpenApiParameter("kind", OpenApiTypes.STR, OpenApiParameter.QUERY, required=True),
            OpenApiParameter("scope", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("period", OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
        responses={200: OpenApiTypes.BINARY},
    )
    def get(self, request):
        role = audience(request.user)
        if role not in {OPERATOR, REGULATOR}:
            raise PermissionDenied("Only the compliance desk and regulators can compile reports.")

        kind = request.query_params.get("kind")
        if kind not in reports.ReportKind.LABELS:
            raise AppError("Unknown report kind.", code="unknown_report_kind")
        scope = request.query_params.get("scope") or reports.ALL_REGIONS
        period = request.query_params.get("period", "last_quarter")
        if period not in reports.PERIODS:
            raise AppError("Unknown reporting period.", code="unknown_period")

        from mining.models import generate_inspection_reference

        reference = generate_inspection_reference().replace("BLM-INS", "BLM-RPT")
        pdf, _pages, _period_label = reports.build(kind, scope, period, reference=reference, generated_by=request.user)

        audit.record(
            request, "report_generated", target=reference,
            detail=f"{reports.ReportKind.LABELS[kind]} · {scope} · {period}.",
        )
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{reference}.pdf"'
        return response


class MiningOrganisationVerificationView(APIView):
    """The desk's register of mining companies, and the decision on each."""

    permission_classes = [IsMiningParticipant]

    def _require_desk(self, request):
        if not can_decide(request.user):
            raise PermissionDenied("Only the compliance operator desk can verify an organisation.")

    def _row(self, organisation):
        return {
            "id": str(organisation.id),
            "name": organisation.name,
            "beldium_id": organisation.beldium_id,
            "verification_status": organisation.verification_status,
            "verified_at": organisation.verified_at,
            "rejection_reason": organisation.rejection_reason,
            **verification.readiness(organisation),
        }

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        if audience(request.user) not in {"operator", "regulator"}:
            raise PermissionDenied("Only the desk and the regulator can see the organisation register.")
        organisations = Organisation.objects.filter(organisation_type="mining_company").order_by("name")
        return Response({"results": [self._row(o) for o in organisations]})

    @extend_schema(request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    @transaction.atomic
    def post(self, request, pk=None, decision=None):
        self._require_desk(request)
        organisation = get_object_or_404(Organisation, pk=pk, organisation_type="mining_company")
        if decision == "verify":
            state = verification.readiness(organisation)
            if not state["ready"]:
                raise AppError(
                    "This organisation is not ready to verify: " + " ".join(state["blockers"]),
                    code="organisation_not_ready", status_code=409, details=state,
                )
            organisation.verification_status = "verified"
            organisation.verified_at = timezone.now()
            organisation.verified_by = request.user
            organisation.rejection_reason = ""
            if not organisation.submitted_at:
                organisation.submitted_at = organisation.verified_at
            event = "organisation.verified"
        else:
            reason = str(request.data.get("reason", "")).strip()
            if not reason:
                raise AppError("Give a reason for rejecting this organisation.", code="reason_required")
            organisation.verification_status = "rejected"
            organisation.verified_at = None
            organisation.verified_by = request.user
            organisation.rejection_reason = reason
            event = "organisation.rejected"
        organisation.save(update_fields=[
            "verification_status", "verified_at", "verified_by", "rejection_reason", "submitted_at", "updated_at",
        ])
        record_account_event(request, event, organisation_id=str(organisation.id))
        return Response(self._row(organisation))
