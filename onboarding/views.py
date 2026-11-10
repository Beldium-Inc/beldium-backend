from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import ConflictError
from common.files import serve_stored_file
from onboarding import services
from onboarding.models import (
    OnboardingDashboard,
    ParticipantApplication,
    ParticipantDocument,
    RegulatorAccessRequest,
)
from onboarding.participants import ADMIN_SECTIONS, SECTIONS
from onboarding.serializers import (
    OnboardingOptionSerializer,
    ParticipantApplicationSerializer,
    ParticipantDocumentUploadSerializer,
    ParticipantRegisterSerializer,
    RegulatorAccessRequestSerializer,
    RegulatorApproveSerializer,
    RegulatorRejectSerializer,
    clean_section,
)
from organisations import rights
from organisations.audit import record_event
from organisations.models import ADMIN_ROLES, SECTOR_SLUGS, OrganisationMembership


class OnboardingOptionsView(APIView):
    """What someone can choose on a sector's onboarding screen (BE-O1).

    Open before sign in: the screen is shown before an account exists.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[
            OpenApiParameter("sector", OpenApiTypes.STR, required=True, enum=SECTOR_SLUGS),
            OpenApiParameter("dashboard", OpenApiTypes.STR, required=False, enum=["applicant", "partner"]),
        ],
        responses=OpenApiTypes.OBJECT,
    )
    def get(self, request):
        sector = request.query_params.get("sector", "")
        if sector not in SECTOR_SLUGS:
            raise ValidationError({"sector": [f"Choose one of: {', '.join(SECTOR_SLUGS)}."]})
        dashboards = OnboardingDashboard.objects.filter(sector=sector, enabled=True).prefetch_related("options")
        only = request.query_params.get("dashboard")
        if only:
            dashboards = dashboards.filter(key=only)
        payload = []
        for dashboard in dashboards:
            options = [option for option in dashboard.options.all() if option.enabled]
            payload.append({
                "key": dashboard.key,
                "label": dashboard.label,
                "description": dashboard.description,
                "options": OnboardingOptionSerializer(options, many=True).data,
            })
        return Response({"sector": sector, "dashboards": payload})


class OnboardingMeView(APIView):
    """The same body every sector's me/ carries, for a caller who has not
    picked a sector dashboard yet (BE-O6)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(parameters=[OpenApiParameter("sector", OpenApiTypes.STR, required=True, enum=SECTOR_SLUGS)], responses=OpenApiTypes.OBJECT)
    def get(self, request):
        sector = request.query_params.get("sector", "")
        if sector not in SECTOR_SLUGS:
            raise ValidationError({"sector": [f"Choose one of: {', '.join(SECTOR_SLUGS)}."]})
        return Response(rights.build_me(request.user, sector))


class ParticipantApplicationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Register one of a sector's own companies and fill in its draft (BE-O3).

    Members of the company see and edit its application; staff of Beldium
    see every application. Anyone else gets 404, so ids cannot be probed.
    """

    serializer_class = ParticipantApplicationSerializer
    permission_classes = [IsAuthenticated]
    queryset = ParticipantApplication.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = ParticipantApplication.objects.select_related("organisation").prefetch_related("documents")
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(organisation__memberships__user=user, organisation__memberships__is_active=True).distinct()

    def _membership(self, application):
        return OrganisationMembership.objects.filter(
            organisation=application.organisation, user=self.request.user, is_active=True,
        ).first()

    def _locked(self, pk):
        """The application, locked for this transaction, checked against the
        caller's own memberships rather than any id they sent."""
        visible = get_object_or_404(self.get_queryset(), pk=pk)
        application = (
            ParticipantApplication.objects.select_for_update(of=("self",))
            .select_related("organisation").get(pk=visible.pk)
        )
        membership = self._membership(application)
        if membership is None:
            # Beldium staff read every application but do not fill one in.
            raise PermissionDenied("Only members of this company may change its application.")
        if application.status != ParticipantApplication.Status.DRAFT:
            raise ConflictError("This application has been submitted and can no longer be changed.", code="application_submitted")
        return application, membership

    @extend_schema(request=ParticipantRegisterSerializer, responses={201: ParticipantApplicationSerializer})
    def create(self, request):
        serializer = ParticipantRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = services.register_participant(request, **serializer.validated_data)
        return Response(self.get_serializer(application).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("section", OpenApiTypes.STR, OpenApiParameter.PATH, enum=SECTIONS)],
        request=OpenApiTypes.OBJECT,
        responses=ParticipantApplicationSerializer,
    )
    @action(detail=True, methods=["put", "patch"], url_path=r"sections/(?P<section>[a-z]+)")
    @transaction.atomic
    def save_section(self, request, pk=None, section=None):
        """Replace one section with the body sent. Company, representative
        and declarations are the admin's; operations and sites any member's."""
        if section not in SECTIONS:
            raise NotFound("No such section.")
        application, membership = self._locked(pk)
        if section in ADMIN_SECTIONS and membership.role not in ADMIN_ROLES:
            raise PermissionDenied("Only your company admin can do this.")
        data = request.data
        if section != "sites" and hasattr(data, "dict"):
            data = data.dict()
        value = clean_section(section, data)
        setattr(application, section, value)
        fields = [section, "updated_at"]
        if section == "operations" and application.sector == "marketplace":
            answer = str(value.get("participantType") or "").strip().lower()
            if answer in {"buyer", "offtaker", "oem"} and answer != application.participant_subtype:
                application.participant_subtype = answer
                application.organisation.participant_subtype = answer
                application.organisation.save(update_fields=["participant_subtype", "updated_at"])
                fields.append("participant_subtype")
        if section == "company":
            services.sync_company_profile(application.organisation, value)
        application.save(update_fields=fields)
        record_event(request, "participant.section_saved", organisation=application.organisation, section=section)
        return Response(self.get_serializer(application).data)

    @extend_schema(request={"multipart/form-data": ParticipantDocumentUploadSerializer}, responses=ParticipantApplicationSerializer)
    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser, FormParser])
    @transaction.atomic
    def documents(self, request, pk=None):
        """Upload one document under the title the sector asks for. Any
        member may upload; a second upload under a title replaces the first."""
        application, _membership = self._locked(pk)
        serializer = ParticipantDocumentUploadSerializer(data=request.data, context={"application": application})
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        document, created = ParticipantDocument.objects.get_or_create(
            application=application, title=serializer.validated_data["title"],
            defaults={"file": upload, "original_name": upload.name[:255], "uploaded_by": request.user},
        )
        if not created:
            document.file = upload
            document.original_name = upload.name[:255]
            document.uploaded_by = request.user
            document.save(update_fields=["file", "original_name", "uploaded_by", "updated_at"])
        record_event(request, "participant.document_uploaded", organisation=application.organisation, title=document.title)
        application = self.get_queryset().get(pk=application.pk)
        return Response(self.get_serializer(application).data, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("document_id", OpenApiTypes.UUID, OpenApiParameter.PATH)], responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"], url_path=r"documents/(?P<document_id>[0-9a-f-]+)/download")
    def download_document(self, request, pk=None, document_id=None):
        application = self.get_object()
        document = get_object_or_404(application.documents, pk=document_id)
        return serve_stored_file(document.file, document.original_name or document.title)

    @extend_schema(request=None, responses=ParticipantApplicationSerializer)
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def submit(self, request, pk=None):
        """Only an admin may submit, and only a complete application."""
        application, membership = self._locked(pk)
        if membership.role not in ADMIN_ROLES:
            raise PermissionDenied("Only your company admin can do this.")
        missing = self.get_serializer(application).data["missing"]
        if missing:
            raise ValidationError({"missing": missing, "detail": "The application is not complete yet."})
        now = timezone.now()
        application.status = ParticipantApplication.Status.SUBMITTED
        application.submitted_at = now
        application.submitted_by = request.user
        application.save(update_fields=["status", "submitted_at", "submitted_by", "updated_at"])
        organisation = application.organisation
        if organisation.verification_status in {"draft", "rejected"}:
            organisation.verification_status = "under_review"
            organisation.submitted_at = now
            organisation.rejection_reason = ""
            organisation.save(update_fields=["verification_status", "submitted_at", "rejection_reason", "updated_at"])
        record_event(request, "organisation.submitted", organisation=organisation, sector=application.sector)
        return Response(self.get_serializer(application).data)


class RegulatorAccessRequestViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Regulator access requests (BE-O5, D9).

    Anyone signed in may ask and read their own requests. Only Beldium
    operators (Django staff) list everyone's, approve or reject.
    """

    serializer_class = RegulatorAccessRequestSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    queryset = RegulatorAccessRequest.objects.none()
    filterset_fields = ["status"]

    def get_permissions(self):
        if self.action in {"list", "approve", "reject"}:
            return [IsAdminUser()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = RegulatorAccessRequest.objects.select_related("requester")
        user = self.request.user
        if user.is_staff and self.action not in {"mine"}:
            return qs
        return qs.filter(requester=user)

    def perform_create(self, serializer):
        user = self.request.user
        if RegulatorAccessRequest.objects.filter(requester=user, status=RegulatorAccessRequest.Status.PENDING).exists():
            raise ConflictError("You already have a request waiting for Beldium.", code="regulator_request_pending")
        instance = serializer.save(requester=user)
        record_event(self.request, "regulator.access_requested", request_id=str(instance.id), sectors=instance.requested_sectors)

    @extend_schema(responses=RegulatorAccessRequestSerializer(many=True))
    @action(detail=False, methods=["get"])
    def mine(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)

    @extend_schema(request=RegulatorApproveSerializer, responses=RegulatorAccessRequestSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        access_request = self.get_object()
        serializer = RegulatorApproveSerializer(data=request.data, context={"access_request": access_request})
        serializer.is_valid(raise_exception=True)
        access_request = services.approve_regulator_request(request, access_request, serializer.validated_data["sectors"])
        return Response(self.get_serializer(access_request).data)

    @extend_schema(request=RegulatorRejectSerializer, responses=RegulatorAccessRequestSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        access_request = self.get_object()
        serializer = RegulatorRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        access_request = services.reject_regulator_request(request, access_request, serializer.validated_data["reason"])
        return Response(self.get_serializer(access_request).data)

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"])
    def letter(self, request, pk=None):
        access_request = self.get_object()
        if not access_request.authority_letter:
            raise NotFound("No letter was attached to this request.")
        name = access_request.authority_letter.name.rsplit("/", 1)[-1]
        return serve_stored_file(access_request.authority_letter, name)
