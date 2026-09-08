from django.db import transaction
from rest_framework.generics import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from pathlib import Path

from django.http import FileResponse, HttpResponseRedirect

from accounts.audit import record_account_event
from accounts.models import AccountAuditEvent
from compliance.models import ApprovalCondition, MessageReadReceipt, ApplicationMessage, ApplicationStatus, ComplianceApplication, ComplianceDocument, Personnel, REQUIRED_DOCUMENTS
from compliance.permissions import IsApplicationMember
from compliance.serializers import (
    ApplicationActivitySerializer,
    ApprovalConditionSerializer, ConditionEvidenceSerializer,
    ApplicationDecisionSerializer, ApplicationMessageSerializer, ComplianceApplicationSerializer,
    ComplianceDocumentSerializer, ComplianceDocumentUploadSerializer, DocumentReviewSerializer, PersonnelSerializer,
    RequestedDocumentSerializer, application_progress,
    DashboardResponseSerializer,
    OrganisationSectionSerializer,
    RepresentativeSectionSerializer, ServicesSectionSerializer,
    ProfessionalCapabilitySectionSerializer, InspectionCapabilitySectionSerializer,
    ConflictDeclarationSectionSerializer, DeclarationSectionSerializer,
)
from compliance.workflow import require_review, transition
from common.exceptions import AppError, ConflictError
from organisations.models import MembershipRole


EDIT_ROLES = {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.COMPLIANCE_MANAGER}
class ComplianceApplicationViewSet(viewsets.ModelViewSet):
    queryset = ComplianceApplication.objects.none()
    serializer_class = ComplianceApplicationSerializer
    permission_classes = [IsApplicationMember]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    # Without these the project's DjangoFilterBackend has nothing to act on and
    # ?status= / ?organisation= are accepted but silently ignored.
    filterset_fields = ["status", "organisation"]

    @extend_schema(exclude=True)
    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed("DELETE")

    @transaction.atomic
    def dispatch(self, request, *args, **kwargs):
        # DRF turns exceptions into responses; explicitly roll back failed actions.
        response = super().dispatch(request, *args, **kwargs)
        if response.status_code >= 400:
            transaction.set_rollback(True)
        return response

    def get_object(self):
        obj = super().get_object()
        if self.request.method not in {"GET", "HEAD", "OPTIONS"}:
            obj = ComplianceApplication.objects.select_for_update().get(pk=obj.pk)
        return obj

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset
        qs = ComplianceApplication.objects.select_related("organisation", "created_by").prefetch_related("personnel", "documents", "conditions__evidence")
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
        setattr(application, model_field, serializer.data["data"])
        application.save(update_fields=[model_field, "updated_at"])
        self._record(application, "section_saved", section=model_field)
        return Response(self.get_serializer(application).data)

    def perform_create(self, serializer):
        organisation = serializer.validated_data["organisation"]
        if not self.request.user.is_staff and not organisation.memberships.filter(user=self.request.user, is_active=True, role__in=EDIT_ROLES).exists():
            raise PermissionDenied("Your organisation role cannot create an application.")
        application = serializer.save(created_by=self.request.user)
        record_account_event(self.request, "compliance.application_created", organisation_id=str(application.organisation_id), application_id=str(application.id))

    @extend_schema(request=OrganisationSectionSerializer, responses={status.HTTP_200_OK: ComplianceApplicationSerializer})
    @action(detail=True, methods=["patch"], url_path="sections/organisation", url_name="organisation-section")
    def organisation_section(self, request, pk=None):
        application = self.get_object()
        self._assert_editor(application)
        self._assert_editable(application)
        serializer = OrganisationSectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.data["data"]
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

    @extend_schema(methods=["GET"], responses=ComplianceDocumentSerializer(many=True))
    @extend_schema(
        methods=["POST"], request=ComplianceDocumentUploadSerializer,
        responses={200: ComplianceDocumentSerializer, 201: ComplianceDocumentSerializer},
        description="Upload or replace a checklist/requested document. Send document_type and file as multipart/form-data. PDF, Word, JPEG and PNG are supported, up to 10 MB. The title is assigned by the server.",
    )
    @action(detail=True, methods=["get", "post"], parser_classes=[MultiPartParser], pagination_class=None)
    def documents(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(ComplianceDocumentSerializer(application.documents.all(), many=True, context={"request": request}).data)
        self._assert_editor(application)
        self._assert_editable(application)
        if "file" not in request.FILES:
            raise AppError("A replacement file is required.", code="validation_error")
        document_type = request.data.get("document_type", "")
        required = dict(REQUIRED_DOCUMENTS)
        existing = application.documents.filter(document_type=document_type).first()
        if document_type not in required and not existing:
            raise AppError("This document type has not been requested.", code="unknown_document_type")
        serializer = ComplianceDocumentUploadSerializer(existing, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        title = required[document_type] if document_type in required else existing.title
        document = serializer.save(application=application, title=title, status=ComplianceDocument.Status.SUBMITTED, review_notes="", reviewed_at=None, reviewed_by=None)
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
        requirements = []
        for key, title in REQUIRED_DOCUMENTS:
            document = existing.pop(key, None)
            requirements.append({"document_type": key, "title": title, "required": True,
                                 "status": document.status if document else "not_submitted",
                                 "due_date": document.due_date if document else None})
        requirements.extend({"document_type": doc.document_type, "title": doc.title, "required": True,
                             "status": doc.status, "due_date": doc.due_date} for doc in existing.values())
        return Response(requirements)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def submit(self, request, pk=None):
        application = self.get_object()
        self._assert_editor(application)
        self._assert_editable(application)
        progress = application_progress(application)
        if progress["percent"] != 100:
            raise AppError("Complete every onboarding section before submitting.", code="application_incomplete", status_code=409, details=progress)
        application.submitted_at = timezone.now()
        transition(application, ApplicationStatus.UNDER_REVIEW)
        record_account_event(request, "compliance.application_submitted", organisation_id=str(application.organisation_id), application_id=str(application.id))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=ApplicationDecisionSerializer, responses=ComplianceApplicationSerializer)
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def decide(self, request, pk=None):
        application = self.get_object()
        require_review(application)
        serializer = ApplicationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target = serializer.validated_data["status"]
        if target == ApplicationStatus.UNDER_REVIEW:
            raise ConflictError("The applicant must resubmit using the submit endpoint.", code="resubmission_required")
        if target in {ApplicationStatus.VERIFIED, ApplicationStatus.CONDITIONALLY_APPROVED}:
            progress = application_progress(application)
            if progress["percent"] != 100:
                raise AppError("The application is incomplete.", code="application_incomplete", status_code=409, details=progress)
            if application.documents.exclude(status="verified").exists():
                raise ConflictError("Review and verify every document before approval.", code="documents_not_verified")
        new_conditions = serializer.validated_data.get("conditions", [])
        if target == ApplicationStatus.CONDITIONALLY_APPROVED and not new_conditions and not application.conditions.exclude(status="cleared").exists():
            raise AppError("Provide at least one approval condition with a deadline.", code="conditions_required")
        if target == ApplicationStatus.VERIFIED and application.conditional_requirements and not application.conditions.exists():
            raise ConflictError("Convert legacy approval conditions into structured conditions before verification.", code="conditions_required")
        if target == ApplicationStatus.VERIFIED and application.conditions.exclude(status="cleared").exists():
            raise ConflictError("Clear all approval conditions before verification.", code="conditions_not_cleared")
        application.review_notes = serializer.validated_data.get("notes", "")
        application.conditional_requirements = serializer.validated_data.get("conditional_requirements", application.conditional_requirements)
        application.reviewed_by = request.user
        application.reviewed_at = timezone.now()
        transition(application, target, reviewer=request.user)
        for condition in new_conditions:
            ApprovalCondition.objects.create(application=application, created_by=request.user, **condition)
        record_account_event(request, "compliance.application_decided", application_id=str(application.id), organisation_id=str(application.organisation_id), status=target)
        return Response(self.get_serializer(application).data)

    @extend_schema(request=RequestedDocumentSerializer, responses=RequestedDocumentSerializer)
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="request-document")
    def request_document(self, request, pk=None):
        application = self.get_object()
        require_review(application)
        serializer = RequestedDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document, _ = ComplianceDocument.objects.update_or_create(
            application=application, document_type=serializer.validated_data["document_type"],
            defaults={"title": serializer.validated_data["title"], "request_message": serializer.validated_data.get("request_message", ""), "status": ComplianceDocument.Status.REQUESTED, "requested_by": request.user, "due_date": serializer.validated_data.get("due_date"), "review_notes": "", "reviewed_at": None, "reviewed_by": None},
        )
        if application.status != ApplicationStatus.ACTION_REQUIRED:
            transition(application, ApplicationStatus.ACTION_REQUIRED)
        record_account_event(request, "compliance.document_requested", application_id=str(application.id), organisation_id=str(application.organisation_id), document_id=str(document.id))
        return Response(RequestedDocumentSerializer(document).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DocumentReviewSerializer, responses=ComplianceDocumentSerializer, parameters=[OpenApiParameter("document_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path=r"documents/(?P<document_id>[^/.]+)/review")
    def review_document(self, request, pk=None, document_id=None):
        application = self.get_object()
        require_review(application)
        document = get_object_or_404(application.documents, id=document_id)
        if not document.file or document.status != "submitted":
            raise ConflictError("Only submitted documents can be reviewed.", code="document_not_submitted")
        serializer = DocumentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document.status = serializer.validated_data["status"]
        document.review_notes = serializer.validated_data.get("notes", "")
        document.reviewed_by = request.user
        document.reviewed_at = timezone.now()
        document.save(update_fields=["status", "review_notes", "reviewed_by", "reviewed_at", "updated_at"])
        if document.status == "rejected" and application.status != ApplicationStatus.ACTION_REQUIRED:
            transition(application, ApplicationStatus.ACTION_REQUIRED)
        record_account_event(request, "compliance.document_reviewed", application_id=str(application.id), organisation_id=str(application.organisation_id), document_id=str(document.id), status=document.status)
        return Response(ComplianceDocumentSerializer(document, context={"request": request}).data)

    @extend_schema(methods=["GET"], responses=ApprovalConditionSerializer(many=True))
    @extend_schema(methods=["POST"], request=ApprovalConditionSerializer, responses=ApprovalConditionSerializer)
    @action(detail=True, methods=["get", "post"])
    def conditions(self, request, pk=None):
        application = self.get_object()
        if request.method == "GET":
            return Response(ApprovalConditionSerializer(application.conditions.all(), many=True, context={"request": request}).data)
        if not request.user.is_staff:
            raise PermissionDenied("Only staff may issue conditions.")
        require_review(application)
        serializer = ApprovalConditionSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        condition = serializer.save(application=application, created_by=request.user)
        record_account_event(request, "compliance.condition_created", application_id=str(application.id), organisation_id=str(application.organisation_id), condition_id=str(condition.id))
        return Response(ApprovalConditionSerializer(condition, context={"request": request}).data, status=201)

    @extend_schema(request=ConditionEvidenceSerializer, responses=ConditionEvidenceSerializer,
                   parameters=[OpenApiParameter("condition_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], url_path=r"conditions/(?P<condition_id>[^/.]+)/evidence", parser_classes=[MultiPartParser, FormParser])
    def condition_evidence(self, request, pk=None, condition_id=None):
        application = self.get_object()
        self._assert_editor(application)
        if application.status not in {ApplicationStatus.CONDITIONALLY_APPROVED, ApplicationStatus.ACTION_REQUIRED}:
            raise ConflictError("Evidence cannot be submitted in this application state.", code="application_locked")
        condition = get_object_or_404(application.conditions, pk=condition_id)
        if condition.status not in {ApprovalCondition.Status.PENDING, ApprovalCondition.Status.REJECTED}:
            raise ConflictError("Evidence is already awaiting review or this condition is cleared.", code="condition_locked")
        serializer = ConditionEvidenceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        evidence = serializer.save(condition=condition, submitted_by=request.user)
        condition.status = ApprovalCondition.Status.SUBMITTED
        condition.save(update_fields=["status", "updated_at"])
        record_account_event(request, "compliance.condition_evidence_submitted", application_id=str(application.id), organisation_id=str(application.organisation_id), condition_id=str(condition.id), evidence_id=str(evidence.id))
        return Response(ConditionEvidenceSerializer(evidence, context={"request": request}).data, status=201)

    @extend_schema(request=DocumentReviewSerializer, responses=ApprovalConditionSerializer,
                   parameters=[OpenApiParameter("condition_id", OpenApiTypes.UUID, OpenApiParameter.PATH), OpenApiParameter("evidence_id", OpenApiTypes.UUID, OpenApiParameter.PATH)])
    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path=r"conditions/(?P<condition_id>[^/.]+)/evidence/(?P<evidence_id>[^/.]+)/review")
    def review_condition_evidence(self, request, pk=None, condition_id=None, evidence_id=None):
        application = self.get_object()
        require_review(application)
        condition = get_object_or_404(application.conditions, pk=condition_id)
        evidence = get_object_or_404(condition.evidence, pk=evidence_id)
        if condition.status != "submitted" or evidence.status != "submitted":
            raise ConflictError("Only pending evidence can be reviewed.", code="evidence_not_submitted")
        serializer = DocumentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        evidence.status = serializer.validated_data["status"]
        evidence.review_notes = serializer.validated_data.get("notes", "")
        evidence.reviewed_by = request.user
        evidence.reviewed_at = timezone.now()
        evidence.save()
        condition.status = ApprovalCondition.Status.CLEARED if evidence.status == "verified" else ApprovalCondition.Status.REJECTED
        condition.save(update_fields=["status", "updated_at"])
        record_account_event(request, "compliance.condition_evidence_reviewed", application_id=str(application.id), organisation_id=str(application.organisation_id), condition_id=str(condition.id), evidence_id=str(evidence.id), status=evidence.status)
        return Response(ApprovalConditionSerializer(condition, context={"request": request}).data)

    @extend_schema(methods=["GET"], responses=ApplicationMessageSerializer(many=True))
    @extend_schema(methods=["POST"], request=ApplicationMessageSerializer, responses=ApplicationMessageSerializer)
    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        application = self.get_object()
        messages = application.messages.all()
        if not request.user.is_staff:
            messages = messages.filter(is_internal=False)
        if request.method == "GET":
            return Response(ApplicationMessageSerializer(messages, many=True, context={"request": request}).data)
        serializer = ApplicationMessageSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        message = serializer.save(application=application, author=request.user)
        return Response(ApplicationMessageSerializer(message, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="messages/mark-read")
    def mark_messages_read(self, request, pk=None):
        application = self.get_object()
        messages = application.messages.exclude(read_receipts__user=request.user).exclude(author=request.user)
        if not request.user.is_staff:
            messages = messages.filter(is_internal=False)
        updated = 0
        for message in messages:
            _, created = MessageReadReceipt.objects.get_or_create(message=message, user=request.user)
            updated += int(created)
        return Response({"marked_read": updated})


class DashboardViewSet(viewsets.ViewSet):
    serializer_class = DashboardResponseSerializer

    def list(self, request):
        applications = ComplianceApplication.objects.filter(organisation__memberships__user=request.user, organisation__memberships__is_active=True).select_related("organisation", "created_by").prefetch_related("documents", "personnel", "conditions__evidence").distinct()
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
                "unread_messages": application.messages.filter(is_internal=False).exclude(read_receipts__user=request.user).exclude(author=request.user).count(),
                "review_notes": application.review_notes, "conditional_requirements": application.conditional_requirements,
                "beldium_id": application.organisation.beldium_id,
                "conditions": ApprovalConditionSerializer(application.conditions.all(), many=True, context={"request": request}).data,
                "requested_documents": ComplianceDocumentSerializer(application.documents.filter(status__in=["requested", "rejected"]), many=True, context={"request": request}).data,
            })
        return Response({"applications": data})
