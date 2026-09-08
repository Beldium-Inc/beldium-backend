from pathlib import Path

from django.db import transaction
from django.http import FileResponse, HttpResponseRedirect
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from accounts.audit import record_account_event
from accounts.models import AccountAuditEvent
from compliance.models import ApplicationMessage, ApplicationStatus, ComplianceApplication, ComplianceDocument, Personnel, REQUIRED_DOCUMENTS
from compliance.permissions import IsApplicationMember
from compliance.serializers import (
    ApplicationActivitySerializer, ApplicationDecisionSerializer, ApplicationMessageSerializer, ComplianceApplicationSerializer,
    ComplianceDocumentSerializer, DocumentReviewSerializer, PersonnelSerializer,
    RequestedDocumentSerializer, application_progress,
    DashboardResponseSerializer,
    OrganisationSectionSerializer,
    RepresentativeSectionSerializer, ServicesSectionSerializer,
    ProfessionalCapabilitySectionSerializer, InspectionCapabilitySectionSerializer,
    ConflictDeclarationSectionSerializer, DeclarationSectionSerializer,
)
from common.exceptions import AppError, ConflictError
from organisations.models import MembershipRole


EDIT_ROLES = {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.COMPLIANCE_MANAGER}
class ComplianceApplicationViewSet(viewsets.ModelViewSet):
    queryset = ComplianceApplication.objects.none()
    serializer_class = ComplianceApplicationSerializer
    # IsApplicationMember only implements has_object_permission, so on its own it
    # leaves list and create ungated — it replaces the project-wide IsAuthenticated
    # default rather than adding to it.
    permission_classes = [IsAuthenticated, IsApplicationMember]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    # Without these the project's DjangoFilterBackend has nothing to act on and
    # ?status= / ?organisation= are accepted but silently ignored.
    filterset_fields = ["status", "organisation"]

    def destroy(self, request, *args, **kwargs):
        # DELETE is allowed on this viewset only so the personnel sub-route can
        # serve it; an application itself is never deleted through the API.
        raise MethodNotAllowed("DELETE")

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = ComplianceApplication.objects.select_related("organisation").prefetch_related("personnel", "documents")
        if self.request.user.is_staff:
            return qs
        return qs.filter(organisation__memberships__user=self.request.user, organisation__memberships__is_active=True).distinct()

    def _record(self, application, event, **metadata):
        """Every compliance event is filed against both the organisation and the
        application, so the organisation audit and the per-application activity
        feed can each find it with one indexed lookup."""
        record_account_event(
            self.request, f"compliance.{event}",
            organisation_id=str(application.organisation_id),
            application_id=str(application.id),
            **metadata,
        )

    def _assert_editor(self, application):
        if self.request.user.is_staff:
            return
        if not application.organisation.memberships.filter(user=self.request.user, is_active=True, role__in=EDIT_ROLES).exists():
            raise PermissionDenied("Your organisation role cannot edit this application.")

    def _assert_editable(self, application):
        if application.status not in {ApplicationStatus.DRAFT, ApplicationStatus.ACTION_REQUIRED, ApplicationStatus.REJECTED}:
            raise ConflictError("This application cannot currently be edited.", code="application_locked")

    def _save_section(self, request, application, serializer_class, model_field):
        self._assert_editor(application)
        self._assert_editable(application)
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        setattr(application, model_field, serializer.validated_data["data"])
        application.save(update_fields=[model_field, "updated_at"])
        self._record(application, "section_saved", section=model_field)
        return Response(self.get_serializer(application).data)

    def perform_create(self, serializer):
        organisation = serializer.validated_data["organisation"]
        if not self.request.user.is_staff and not organisation.memberships.filter(user=self.request.user, is_active=True, role__in=EDIT_ROLES).exists():
            raise PermissionDenied("Your organisation role cannot create an application.")
        application = serializer.save()
        self._record(application, "application_created")

    @extend_schema(request=OrganisationSectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/organisation", url_name="organisation-section")
    def organisation_section(self, request, pk=None):
        application = self.get_object()
        self._assert_editor(application)
        self._assert_editable(application)
        serializer = OrganisationSectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data["data"]
        application.organisation_profile = data
        application.organisation_profile["year_established"] = data["year_established"]
        application.save(update_fields=["organisation_profile", "updated_at"])

        organisation = application.organisation
        organisation.name = data["name"]
        organisation.organisation_type = data["organisation_type"]
        organisation.registration_number = data["registration_number"]
        organisation.tax_identifier = data["tax_identifier"]
        organisation.website = data.get("website", "")
        organisation.address = data["registered_address"]
        organisation.country = data["country"]
        organisation.state = data["state"]
        organisation.save(update_fields=[
            "name", "organisation_type", "registration_number", "tax_identifier", "website",
            "address", "country", "state", "updated_at",
        ])
        self._record(application, "section_saved", section="organisation_profile")
        return Response(self.get_serializer(application).data)

    @extend_schema(request=RepresentativeSectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/representative", url_name="representative-section")
    def representative_section(self, request, pk=None):
        return self._save_section(request, self.get_object(), RepresentativeSectionSerializer, "representative")

    @extend_schema(request=ServicesSectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/services", url_name="services-section")
    def services_section(self, request, pk=None):
        return self._save_section(request, self.get_object(), ServicesSectionSerializer, "services")

    @extend_schema(request=ProfessionalCapabilitySectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/professional-capability", url_name="professional-capability-section")
    def professional_capability_section(self, request, pk=None):
        return self._save_section(request, self.get_object(), ProfessionalCapabilitySectionSerializer, "professional_capability")

    @extend_schema(request=InspectionCapabilitySectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/inspection-capability", url_name="inspection-capability-section")
    def inspection_capability_section(self, request, pk=None):
        return self._save_section(request, self.get_object(), InspectionCapabilitySectionSerializer, "inspection_capability")

    @extend_schema(request=ConflictDeclarationSectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/conflict-declaration", url_name="conflict-declaration-section")
    def conflict_declaration_section(self, request, pk=None):
        return self._save_section(request, self.get_object(), ConflictDeclarationSectionSerializer, "conflict_declaration")

    @extend_schema(request=DeclarationSectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/declaration", url_name="declaration-section")
    def declaration_section(self, request, pk=None):
        return self._save_section(request, self.get_object(), DeclarationSectionSerializer, "declaration")

    @extend_schema(methods=["GET"], responses={status.HTTP_200_OK: PersonnelSerializer(many=True)})
    @extend_schema(methods=["POST"], request=PersonnelSerializer, responses={status.HTTP_201_CREATED: PersonnelSerializer})
    @action(detail=True, methods=["get", "post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def personnel(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(PersonnelSerializer(application.personnel.all(), many=True, context={"request": request}).data)
        self._assert_editor(application)
        self._assert_editable(application)
        serializer = PersonnelSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        person = serializer.save(application=application)
        self._record(application, "personnel_added", personnel_id=str(person.id), full_name=person.full_name)
        return Response(PersonnelSerializer(person, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        methods=["PATCH"], request=PersonnelSerializer,
        responses={status.HTTP_200_OK: PersonnelSerializer},
        parameters=[OpenApiParameter("personnel_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
    )
    @extend_schema(
        methods=["DELETE"], responses={status.HTTP_204_NO_CONTENT: None},
        parameters=[OpenApiParameter("personnel_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
    )
    @action(detail=True, methods=["patch", "delete"], url_path=r"personnel/(?P<personnel_id>[^/.]+)", parser_classes=[MultiPartParser, FormParser, JSONParser])
    def personnel_detail(self, request, pk=None, personnel_id=None):
        application = self.get_object()
        self._assert_editor(application)
        self._assert_editable(application)
        person = application.personnel.filter(id=personnel_id).first()
        if not person:
            raise AppError("Personnel record not found.", code="not_found", status_code=404)
        if request.method == "DELETE":
            full_name = person.full_name
            person.delete()
            self._record(application, "personnel_removed", personnel_id=str(personnel_id), full_name=full_name)
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = PersonnelSerializer(person, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self._record(application, "personnel_updated", personnel_id=str(person.id), full_name=person.full_name)
        return Response(serializer.data)

    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser, FormParser])
    def documents(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(ComplianceDocumentSerializer(application.documents.all(), many=True, context={"request": request}).data)
        self._assert_editor(application)
        self._assert_editable(application)
        document_type = request.data.get("document_type", "")
        required = dict(REQUIRED_DOCUMENTS)
        existing = application.documents.filter(document_type=document_type).first()
        if document_type not in required and not existing:
            raise AppError("This document type has not been requested.", code="unknown_document_type")
        mutable_data = request.data.copy()
        if document_type in required:
            mutable_data["title"] = required[document_type]
        serializer = ComplianceDocumentSerializer(existing, data=mutable_data, partial=bool(existing), context={"request": request})
        serializer.is_valid(raise_exception=True)
        document = serializer.save(application=application, status=ComplianceDocument.Status.SUBMITTED, review_notes="", reviewed_at=None, reviewed_by=None)
        self._record(application, "document_uploaded", document_type=document.document_type, title=document.title)
        return Response(ComplianceDocumentSerializer(document, context={"request": request}).data, status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED)

    def _serve(self, stored_file, filename):
        """Hand back a stored file without exposing the storage layer.

        S3-backed storage signs an absolute URL that is already access
        controlled and expires on its own, so redirecting to it is both cheaper
        and safer than proxying the bytes. Local storage returns a bare
        site-relative path with nothing guarding it, so those bytes are streamed
        through this view, which has already checked the caller.
        """
        url = stored_file.url
        if url.startswith(("http://", "https://")):
            return HttpResponseRedirect(url)
        return FileResponse(stored_file.open("rb"), as_attachment=True, filename=filename)

    @extend_schema(parameters=[OpenApiParameter("document_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["get"], url_path=r"documents/(?P<document_id>[^/.]+)/download", url_name="download-document")
    def download_document(self, request, pk=None, document_id=None):
        application = self.get_object()
        document = application.documents.filter(id=document_id).first()
        if not document or not document.file:
            raise AppError("Document not found.", code="not_found", status_code=404)
        return self._serve(document.file, document.original_name)

    @extend_schema(parameters=[OpenApiParameter("personnel_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(
        detail=True, methods=["get"],
        url_path=r"personnel/(?P<personnel_id>[^/.]+)/download/(?P<field>cv|certificate)",
        url_name="download-personnel-file",
    )
    def download_personnel_file(self, request, pk=None, personnel_id=None, field=None):
        application = self.get_object()
        person = application.personnel.filter(id=personnel_id).first()
        if not person:
            raise AppError("Personnel record not found.", code="not_found", status_code=404)
        stored_file = getattr(person, field)
        if not stored_file:
            raise AppError("No file has been uploaded for this record.", code="not_found", status_code=404)
        return self._serve(stored_file, Path(stored_file.name).name)

    @extend_schema(responses={status.HTTP_200_OK: ApplicationActivitySerializer(many=True)})
    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        """What has happened to this application, readable by its own members.

        Deliberately not the organisation audit endpoint: that one is scoped to
        the organisation, restricted to its administrators, and serialises the
        actor's email and IP address, none of which an applicant should be shown
        about a reviewer.
        """
        application = self.get_object()
        events = AccountAuditEvent.objects.filter(
            metadata__application_id=str(application.id)
        ).select_related("actor")
        page = self.paginate_queryset(events)
        serializer = ApplicationActivitySerializer(page if page is not None else events, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="document-requirements")
    def document_requirements(self, request, pk=None):
        application = self.get_object()
        existing = {item.document_type: item for item in application.documents.all()}
        return Response([
            {"document_type": key, "title": title, "required": True, "status": existing[key].status if key in existing else "not_submitted"}
            for key, title in REQUIRED_DOCUMENTS
        ])

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def submit(self, request, pk=None):
        application = self.get_object()
        self._assert_editor(application)
        self._assert_editable(application)
        progress = application_progress(application)
        if progress["percent"] != 100:
            raise AppError("Complete every onboarding section before submitting.", code="application_incomplete", status_code=409, details=progress)
        application.status = ApplicationStatus.UNDER_REVIEW
        application.submitted_at = timezone.now()
        application.save(update_fields=["status", "submitted_at", "updated_at"])
        application.organisation.verification_status = "under_review"
        application.organisation.submitted_at = application.submitted_at
        application.organisation.save(update_fields=["verification_status", "submitted_at", "updated_at"])
        self._record(application, "application_submitted")
        return Response(self.get_serializer(application).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def decide(self, request, pk=None):
        application = self.get_object()
        # Nothing can move an application back to draft, so draft means it was
        # never submitted: there is no review to record a decision against, and
        # its submitted_at would stay null while reviewed_at was set.
        if application.status == ApplicationStatus.DRAFT:
            raise ConflictError(
                "This application has not been submitted for review.",
                code="application_not_submitted",
            )
        serializer = ApplicationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application.status = serializer.validated_data["status"]
        application.review_notes = serializer.validated_data.get("notes", "")
        application.conditional_requirements = serializer.validated_data.get("conditional_requirements", "")
        application.reviewed_by = request.user
        application.reviewed_at = timezone.now()
        application.save(update_fields=["status", "review_notes", "conditional_requirements", "reviewed_by", "reviewed_at", "updated_at"])
        organisation_status = {
            ApplicationStatus.VERIFIED: "verified", ApplicationStatus.REJECTED: "rejected",
            ApplicationStatus.UNDER_REVIEW: "under_review", ApplicationStatus.ACTION_REQUIRED: "under_review",
            ApplicationStatus.CONDITIONALLY_APPROVED: "under_review",
        }[application.status]
        application.organisation.verification_status = organisation_status
        application.organisation.verified_at = application.reviewed_at if application.status == ApplicationStatus.VERIFIED else None
        application.organisation.verified_by = request.user
        application.organisation.rejection_reason = application.review_notes if application.status == ApplicationStatus.REJECTED else ""
        application.organisation.save(update_fields=["verification_status", "verified_at", "verified_by", "rejection_reason", "updated_at"])
        self._record(application, "application_decided", decision=application.status)
        return Response(self.get_serializer(application).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="request-document")
    def request_document(self, request, pk=None):
        application = self.get_object()
        serializer = RequestedDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document, _ = ComplianceDocument.objects.update_or_create(
            application=application, document_type=serializer.validated_data["document_type"],
            defaults={"title": serializer.validated_data["title"], "request_message": serializer.validated_data.get("request_message", ""), "status": ComplianceDocument.Status.REQUESTED, "requested_by": request.user, "file": ""},
        )
        application.status = ApplicationStatus.ACTION_REQUIRED
        application.save(update_fields=["status", "updated_at"])
        self._record(application, "document_requested", document_type=document.document_type, title=document.title)
        return Response(RequestedDocumentSerializer(document).data, status=status.HTTP_201_CREATED)

    @extend_schema(parameters=[OpenApiParameter("document_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path=r"documents/(?P<document_id>[^/.]+)/review")
    def review_document(self, request, pk=None, document_id=None):
        application = self.get_object()
        document = application.documents.filter(id=document_id).first()
        if not document:
            raise AppError("Document not found.", code="not_found", status_code=404)
        serializer = DocumentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document.status = serializer.validated_data["status"]
        document.review_notes = serializer.validated_data.get("notes", "")
        document.reviewed_by = request.user
        document.reviewed_at = timezone.now()
        document.save(update_fields=["status", "review_notes", "reviewed_by", "reviewed_at", "updated_at"])
        self._record(application, "document_reviewed", document_type=document.document_type, outcome=document.status)
        return Response(ComplianceDocumentSerializer(document, context={"request": request}).data)

    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        application = self.get_object()
        messages = application.messages.all()
        if not request.user.is_staff:
            messages = messages.filter(is_internal=False)
        if request.method == "GET":
            return Response(ApplicationMessageSerializer(messages, many=True).data)
        serializer = ApplicationMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = serializer.save(application=application, author=request.user, is_internal=bool(request.data.get("is_internal", False) and request.user.is_staff))
        return Response(ApplicationMessageSerializer(message).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="messages/mark-read")
    def mark_messages_read(self, request, pk=None):
        application = self.get_object()
        messages = application.messages.filter(read_at__isnull=True).exclude(author=request.user)
        if not request.user.is_staff:
            messages = messages.filter(is_internal=False)
        updated = messages.update(read_at=timezone.now())
        return Response({"marked_read": updated})


class DashboardViewSet(viewsets.ViewSet):
    serializer_class = DashboardResponseSerializer

    def list(self, request):
        applications = ComplianceApplication.objects.filter(organisation__memberships__user=request.user, organisation__memberships__is_active=True).select_related("organisation").prefetch_related("documents", "personnel").distinct()
        data = []
        for application in applications:
            progress = application_progress(application)
            data.append({
                "application_id": application.id, "reference": application.reference,
                "organisation_id": application.organisation_id,
                "organisation_name": application.organisation.name, "status": application.status,
                "progress": progress, "personnel_count": application.personnel.count(),
                "documents_submitted": progress["documents"]["submitted"],
                "documents_required": progress["documents"]["required"],
                "documents_requested": application.documents.filter(status=ComplianceDocument.Status.REQUESTED).count(),
                "unread_messages": application.messages.filter(read_at__isnull=True, is_internal=False).exclude(author=request.user).count(),
                "review_notes": application.review_notes, "conditional_requirements": application.conditional_requirements,
            })
        return Response({"applications": data})
