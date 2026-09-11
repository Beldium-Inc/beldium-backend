import csv
import io
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from accounts.models import AccountAuditEvent
from organisations.models import OrganisationMembership
from common.exceptions import ConflictError
from logistics import models as m, serializers as s, services
from logistics.permissions import company_ids, can_review, can_edit, assert_editor, assert_reviewer, can_see_driver_details, EDIT_ROLES


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
        if self.request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            obj = type(obj).objects.select_for_update().get(pk=obj.pk)
        return obj

    def context(self, **extra):
        return {**self.get_serializer_context(), **extra}


def editable_company(user, company):
    assert_editor(user, company)
    application = m.LogisticsApplication.objects.select_for_update().filter(company=company).first()
    if application:
        services.assert_state(application, services.EDITABLE)


class CompanyViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    serializer_class = s.CompanySerializer
    queryset = m.LogisticsCompany.objects.none()
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    search_fields = ['reference', 'organisation__name', 'organisation__registration_number']

    def get_queryset(self):
        return m.LogisticsCompany.objects.filter(pk__in=company_ids(self.request.user)).select_related('organisation')

    def perform_create(self, serializer):
        org = serializer.validated_data['organisation']
        if not self.request.user.is_staff and not OrganisationMembership.objects.filter(
                user=self.request.user, organisation=org, role__in=EDIT_ROLES, is_active=True).exists():
            raise PermissionDenied('You must administer this organisation to register its logistics company.')
        company = serializer.save()
        services.audit(self.request, company, 'company_created')

    def perform_update(self, serializer):
        editable_company(self.request.user, serializer.instance)
        company = serializer.save()
        services.audit(self.request, company, 'company_updated')


class CompanyRecordViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    filterset_fields = ['company']

    def get_queryset(self):
        return self.queryset.model.objects.filter(company_id__in=company_ids(self.request.user)).select_related('company__organisation')

    def perform_create(self, serializer):
        company = serializer.validated_data['company']
        editable_company(self.request.user, company)
        obj = serializer.save()
        services.audit(self.request, company, obj._meta.model_name + '_created', object_id=str(obj.pk))

    def perform_update(self, serializer):
        editable_company(self.request.user, serializer.instance.company)
        obj = serializer.save()
        services.audit(self.request, obj.company, obj._meta.model_name + '_updated', object_id=str(obj.pk))


class LocationViewSet(CompanyRecordViewSet):
    queryset = m.OperatingLocation.objects.none()
    serializer_class = s.LocationSerializer


class VehicleViewSet(CompanyRecordViewSet):
    queryset = m.Vehicle.objects.none()
    serializer_class = s.VehicleSerializer
    search_fields = ['registration', 'vin', 'make', 'model']
    filterset_fields = ['company', 'is_active', 'gps_status']


class DriverViewSet(CompanyRecordViewSet):
    queryset = m.Driver.objects.none()
    serializer_class = s.DriverSerializer
    search_fields = ['full_name']
    filterset_fields = ['company', 'is_active']


class GrantViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin, AtomicViewSet):
    permission_classes = [IsAdminUser]
    queryset = m.LogisticsAccessGrant.objects.all()
    serializer_class = s.GrantSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def perform_create(self, serializer):
        grant = serializer.save()
        services.audit(self.request, grant.company, 'access_granted', user_id=str(grant.user_id), role=grant.role)

    def perform_update(self, serializer):
        grant = serializer.save()
        services.audit(self.request, grant.company, 'access_updated', user_id=str(grant.user_id), active=grant.is_active)


class ApplicationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.LogisticsApplication.objects.none()
    serializer_class = s.ApplicationSerializer
    filterset_fields = ['company', 'status', 'reviewer']

    def get_queryset(self):
        return m.LogisticsApplication.objects.filter(company_id__in=company_ids(self.request.user)).select_related('company__organisation', 'created_by').prefetch_related('sections', 'conditions')

    def perform_create(self, serializer):
        company = serializer.validated_data['company']
        assert_editor(self.request.user, company)
        application = serializer.save(created_by=self.request.user)
        services.initialise(application)
        services.audit(self.request, company, 'application_created', application_id=str(application.pk))

    @extend_schema(request=s.SectionInputSerializer, responses=s.DomainSerializer, parameters=[OpenApiParameter('key', OpenApiTypes.STR, OpenApiParameter.PATH)])
    @action(detail=True, methods=['patch'], url_path=r'sections/(?P<key>[a-z_]+)')
    def section(self, request, pk=None, key=None):
        application = self.get_object()
        assert_editor(request.user, application.company)
        services.assert_state(application, services.EDITABLE)
        section = get_object_or_404(application.sections, key=key)
        payload = s.SectionInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        section.data = payload.validated_data['data']
        section.status, section.score = 'pending', 0
        section.reviewed_by, section.reviewed_at = None, None
        section.save()
        services.audit(request, application.company, 'section_updated', application_id=str(application.pk), domain=key)
        return Response(s.DomainSerializer(section).data)

    @extend_schema(request=s.AssignReviewerSerializer, responses=s.ApplicationSerializer)
    @action(detail=True, methods=['post'], url_path='assign-reviewer')
    def assign_reviewer(self, request, pk=None):
        application = self.get_object()
        if not can_review(request.user, application.company):
            raise PermissionDenied('Only the logistics desk can assign reviewers.')
        services.assert_state(application, {'submitted', 'under_review', 'awaiting_information', 'conditionally_approved'})
        payload = s.AssignReviewerSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        reviewer = payload.validated_data['reviewer']
        if not can_review(reviewer, application.company):
            raise PermissionDenied('The selected user has no review grant for this company.')
        application.reviewer = reviewer
        application.save(update_fields=['reviewer', 'updated_at'])
        services.audit(request, application.company, 'reviewer_assigned', reviewer_id=str(reviewer.pk))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=None, responses=s.ApplicationSerializer)
    @action(detail=True, methods=['post'], url_path='start-review')
    def start_review(self, request, pk=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, {'submitted'})
        application.status = 'under_review'
        application.save(update_fields=['status', 'updated_at'])
        services.audit(request, application.company, 'review_started', application_id=str(application.pk))
        return Response(self.get_serializer(application).data)

    @extend_schema(request=None, responses=s.ApplicationSerializer)
    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        application = self.get_object()
        assert_editor(request.user, application.company)
        services.assert_state(application, services.EDITABLE)
        services.require_complete(application)
        application.status, application.submitted_at = 'submitted', timezone.now()
        application.save(update_fields=['status', 'submitted_at', 'updated_at'])
        services.audit(request, application.company, 'application_submitted', application_id=str(application.pk))
        services.notify(application.company, 'Application submitted', 'A logistics application is ready for review.')
        return Response(self.get_serializer(application).data)

    @extend_schema(request=s.DomainReviewInputSerializer, responses=s.DomainSerializer, parameters=[OpenApiParameter('key', OpenApiTypes.STR, OpenApiParameter.PATH)])
    @action(detail=True, methods=['post'], url_path=r'sections/(?P<key>[a-z_]+)/review')
    def review_section(self, request, pk=None, key=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        section = get_object_or_404(application.sections, key=key)
        payload = s.DomainReviewInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if not data['applicable'] and key != 'mineral':
            raise ConflictError('The baseline policy requires this domain.', code='required_domain')
        if not data['applicable'] and any('mineral' in x.lower() for x in application.company.services):
            raise ConflictError('Mineral transport is required for the declared services.', code='required_domain')
        if data['status'] == 'passed' and data['applicable']:
            documents = application.documents.filter(domain=key, is_current=True)
            if not section.data or not documents.exists() or documents.exclude(status='verified').exists() or documents.filter(expires_on__lt=timezone.localdate()).exists():
                raise ConflictError('Verify current evidence and supply section data before sign-off.', code='domain_evidence_incomplete')
        section.status, section.score, section.applicable = data['status'], data['score'], data['applicable']
        section.review_notes = data['notes']
        section.reviewed_by, section.reviewed_at = request.user, timezone.now()
        section.save()
        services.audit(request, application.company, 'domain_reviewed', domain=key, verdict=section.status)
        return Response(s.DomainSerializer(section).data)

    @extend_schema(methods=['GET'], responses=s.DocumentSerializer(many=True))
    @extend_schema(methods=['POST'], request=s.DocumentUploadSerializer, responses={201: s.DocumentSerializer})
    @action(detail=True, methods=['get', 'post'], parser_classes=[MultiPartParser], pagination_class=None)
    def documents(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            documents = visible_documents(request.user, application.documents.all())
            return Response(s.DocumentSerializer(documents, many=True, context=self.context()).data)
        assert_editor(request.user, application.company)
        services.assert_state(application, services.EDITABLE | {'conditionally_approved', 'approved'})
        payload = s.DocumentUploadSerializer(data=request.data, context=self.context(application=application))
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        previous = application.documents.filter(document_type=data['document_type'], is_current=True).first()
        if application.status == 'conditionally_approved' and not data.get('condition'):
            raise ConflictError('Submit condition evidence or request reopening before changing the application.', code='condition_required')
        if application.status == 'approved' and not previous:
            raise ConflictError('Only renewal of existing credentials is allowed after approval.', code='renewal_required')
        if previous:
            for field in ['domain', 'service_scope', 'vehicle', 'driver', 'condition']:
                if data.get(field, '' if field == 'service_scope' else None) != getattr(previous, field):
                    raise ConflictError('A document version must retain its domain, scope and linked records.', code='document_identity_changed')
            previous.is_current = False
            previous.save(update_fields=['is_current'])
        document = payload.save(application=application, uploaded_by=request.user,
                                original_name=Path(data['file'].name).name,
                                version=previous.version + 1 if previous else 1)
        application.sections.filter(key=document.domain).update(status='pending', score=0, reviewed_at=None, reviewed_by=None)
        if application.status == 'approved':
            application.status = 'awaiting_information'
            application.save(update_fields=['status', 'updated_at'])
        services.audit(request, application.company, 'document_uploaded', document_id=str(document.pk), version=document.version)
        return Response(s.DocumentSerializer(document, context=self.context()).data, status=201)

    @extend_schema(methods=['GET'], responses=s.InformationRequestSerializer(many=True))
    @extend_schema(methods=['POST'], request=s.InformationRequestSerializer, responses={201: s.InformationRequestSerializer})
    @action(detail=True, methods=['get', 'post'], pagination_class=None)
    def requests(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            return Response(s.InformationRequestSerializer(application.requests.all(), many=True).data)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.InformationRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        item = payload.save(application=application, raised_by=request.user)
        application.status = 'awaiting_information'
        application.save(update_fields=['status', 'updated_at'])
        services.notify(application.company, 'Information requested', item.reason)
        services.audit(request, application.company, 'information_requested', request_id=str(item.pk))
        return Response(s.InformationRequestSerializer(item).data, status=201)

    @extend_schema(methods=['GET'], responses=s.ConditionSerializer(many=True))
    @extend_schema(methods=['POST'], request=s.ConditionSerializer, responses={201: s.ConditionSerializer})
    @action(detail=True, methods=['get', 'post'], pagination_class=None)
    def conditions(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            return Response(s.ConditionSerializer(application.conditions.all(), many=True).data)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.ConditionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        condition = add_condition(application, payload.validated_data, request)
        return Response(s.ConditionSerializer(condition).data, status=201)

    @extend_schema(request=s.DecisionSerializer, responses=s.ApplicationSerializer)
    @action(detail=True, methods=['post'])
    def decide(self, request, pk=None):
        application = self.get_object()
        assert_reviewer(request.user, application)
        services.assert_state(application, {'under_review', 'conditionally_approved'})
        payload = s.DecisionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if application.status == data['status']:
            raise ConflictError('That decision has already been recorded.')
        conditional = data['status'] == 'conditionally_approved'
        if data['status'] != 'rejected':
            services.require_approval(application, conditional)
        for condition in data.get('conditions', []):
            add_condition(application, condition, request)
        if conditional and not application.conditions.filter(cleared_at__isnull=True).exists():
            raise ConflictError('Conditional approval requires at least one outstanding condition.')
        application.status, application.rationale = data['status'], data['rationale']
        application.reviewed_at = timezone.now()
        application.save(update_fields=['status', 'rationale', 'reviewed_at', 'updated_at'])
        services.audit(request, application.company, 'decision_recorded', verdict=application.status, rationale=application.rationale)
        services.notify(application.company, 'Application decision', application.status)
        return Response(self.get_serializer(application).data)

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=True, methods=['get'])
    def activity(self, request, pk=None):
        application = self.get_object()
        return Response({'events': audit_rows(application.company_id)})


def add_condition(application, data, request):
    scope = data.get('service_scope', '')
    if scope and scope not in application.company.services:
        raise ConflictError('The condition scope must be a declared service.')
    condition = m.ApprovalCondition.objects.create(application=application, **data)
    if scope:
        m.ScopeRestriction.objects.create(company=application.company, service_scope=scope,
            reason=condition.title, source_condition=condition, applied_by=request.user)
    services.audit(request, application.company, 'condition_created', condition_id=str(condition.pk))
    return condition


def visible_documents(user, queryset):
    if user.is_staff:
        return queryset
    private_ids = [company.pk for company in m.LogisticsCompany.objects.filter(pk__in=company_ids(user)) if can_see_driver_details(user, company)]
    return queryset.filter(Q(application__company_id__in=private_ids) | (~Q(domain='driver') & Q(driver__isnull=True)))


class DocumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.LogisticsDocument.objects.none()
    serializer_class = s.DocumentSerializer
    filterset_fields = ['application', 'domain', 'status', 'is_current']
    search_fields = ['title', 'document_type', 'issuer', 'reference']

    def get_queryset(self):
        return visible_documents(self.request.user, m.LogisticsDocument.objects.filter(application__company_id__in=company_ids(self.request.user)))

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        document = self.get_object()
        response = FileResponse(document.file.open('rb'), as_attachment=True, filename=document.original_name)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response

    @extend_schema(responses=s.DocumentSerializer(many=True))
    @action(detail=False, methods=['get'], pagination_class=None)
    def expiring(self, request):
        from datetime import timedelta
        items = self.get_queryset().filter(is_current=True, expires_on__lte=timezone.localdate() + timedelta(days=30))
        return Response(self.get_serializer(items, many=True).data)

    @extend_schema(request=s.ReviewDocumentInputSerializer, responses=s.DocumentSerializer)
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        document = self.get_object()
        application = m.LogisticsApplication.objects.select_for_update().get(pk=document.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        if not document.is_current or document.status != 'pending':
            raise ConflictError('Only a current pending evidence version can be reviewed.')
        payload = s.ReviewDocumentInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data['status'] == 'verified' and document.expires_on and document.expires_on < timezone.localdate():
            raise ConflictError('Expired evidence cannot be verified.')
        document.status = payload.validated_data['status']
        document.review_notes = payload.validated_data['notes']
        document.reviewed_by, document.reviewed_at = request.user, timezone.now()
        document.save(update_fields=['status', 'review_notes', 'reviewed_by', 'reviewed_at', 'updated_at'])
        if document.status == 'rejected':
            application.sections.filter(key=document.domain).update(status='attention', score=0)
            if application.status != 'conditionally_approved':
                application.status = 'awaiting_information'
                application.save(update_fields=['status', 'updated_at'])
        services.audit(request, application.company, 'document_reviewed', document_id=str(document.pk), verdict=document.status)
        return Response(self.get_serializer(document).data)

    @extend_schema(methods=['GET'], responses=s.NoteSerializer(many=True))
    @extend_schema(methods=['POST'], request=s.NoteSerializer, responses={201: s.NoteSerializer})
    @action(detail=True, methods=['get', 'post'], pagination_class=None)
    def notes(self, request, pk=None):
        document = self.get_object()
        company = document.application.company
        reviewer = can_review(request.user, company)
        if request.method == 'GET':
            notes = document.notes.all() if reviewer else document.notes.filter(internal=False)
            return Response(s.NoteSerializer(notes, many=True).data)
        if not reviewer and not can_edit(request.user, company):
            raise PermissionDenied('You cannot add notes to this document.')
        payload = s.NoteSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data.get('internal') and not reviewer:
            raise PermissionDenied('Internal notes are reserved for reviewers.')
        note = payload.save(document=document, author=request.user)
        services.audit(request, company, 'document_note_added', document_id=str(document.pk))
        return Response(s.NoteSerializer(note).data, status=201)


class RequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.InformationRequest.objects.none()
    serializer_class = s.InformationRequestSerializer
    filterset_fields = ['application', 'status']

    def get_queryset(self):
        return m.InformationRequest.objects.filter(application__company_id__in=company_ids(self.request.user))

    @extend_schema(request=s.RequestResponseSerializer, responses={201: s.RequestResponseSerializer})
    @action(detail=True, methods=['post'])
    def responses(self, request, pk=None):
        item = self.get_object()
        application = m.LogisticsApplication.objects.select_for_update().get(pk=item.application_id)
        assert_editor(request.user, application.company)
        services.assert_state(application, {'awaiting_information'})
        if item.status != 'open':
            raise ConflictError('This request is not open for a response.')
        payload = s.RequestResponseSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        documents = payload.validated_data.get('documents', [])
        if not documents or any(doc.application_id != application.pk or not doc.is_current or doc.status == 'rejected' for doc in documents):
            raise ConflictError('Attach current evidence from this application.')
        response = payload.save(request=item, author=request.user)
        item.status = 'responded'
        item.save(update_fields=['status', 'updated_at'])
        services.notify(application.company, 'Information response received', item.reason)
        services.audit(request, application.company, 'request_responded', request_id=str(item.pk))
        return Response(s.RequestResponseSerializer(response).data, status=201)

    @extend_schema(request=s.ResponseReviewSerializer, responses=s.InformationRequestSerializer)
    @action(detail=True, methods=['post'], url_path='review-response')
    def review_response(self, request, pk=None):
        item = self.get_object()
        application = m.LogisticsApplication.objects.select_for_update().get(pk=item.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        if item.status != 'responded':
            raise ConflictError('There is no unreviewed response.')
        payload = s.ResponseReviewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if payload.validated_data['accepted']:
            latest = item.responses.order_by('-created_at').first()
            if not latest or not latest.documents.exists() or latest.documents.filter(Q(is_current=False) | ~Q(status='verified') | Q(expires_on__lt=timezone.localdate())).exists():
                raise ConflictError('Verify the response evidence before accepting it.')
        item.status = 'accepted' if payload.validated_data['accepted'] else 'open'
        item.review_notes = payload.validated_data['notes']
        item.save(update_fields=['status', 'review_notes', 'updated_at'])
        services.audit(request, application.company, 'request_response_reviewed', request_id=str(item.pk), verdict=item.status)
        return Response(self.get_serializer(item).data)


class ConditionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, AtomicViewSet):
    queryset = m.ApprovalCondition.objects.none()
    serializer_class = s.ConditionSerializer
    filterset_fields = ['application']
    parser_classes = [MultiPartParser]

    def get_queryset(self):
        return m.ApprovalCondition.objects.filter(application__company_id__in=company_ids(self.request.user))

    @extend_schema(request=s.ConditionEvidenceUploadSerializer, responses={201: s.DocumentSerializer})
    @action(detail=True, methods=['post'])
    def evidence(self, request, pk=None):
        condition = self.get_object()
        application = m.LogisticsApplication.objects.select_for_update().get(pk=condition.application_id)
        assert_editor(request.user, application.company)
        services.assert_state(application, {'conditionally_approved', 'awaiting_information'})
        if condition.cleared_at:
            raise ConflictError('This condition has already been cleared.', code='condition_already_cleared')
        payload = s.ConditionEvidenceUploadSerializer(data=request.data, context=self.context(application=application))
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        previous = application.documents.filter(document_type=data['document_type'], is_current=True).first()
        if previous:
            changed = (
                previous.domain != data['domain'] or
                previous.service_scope != condition.service_scope or
                previous.condition_id != condition.pk or
                previous.vehicle_id is not None or
                previous.driver_id is not None
            )
            if changed:
                raise ConflictError('A condition evidence version must retain its domain, scope and linked condition.', code='document_identity_changed')
            previous.is_current = False
            previous.save(update_fields=['is_current'])
        document = payload.save(application=application, condition=condition, uploaded_by=request.user,
                                service_scope=condition.service_scope,
                                original_name=Path(data['file'].name).name,
                                version=previous.version + 1 if previous else 1)
        application.sections.filter(key=document.domain).update(status='pending', score=0, reviewed_at=None, reviewed_by=None)
        services.audit(request, application.company, 'condition_evidence_uploaded', condition_id=str(condition.pk), document_id=str(document.pk), version=document.version)
        return Response(s.DocumentSerializer(document, context=self.context()).data, status=201)

    @extend_schema(request=s.ResolutionSerializer, responses=s.ConditionSerializer)
    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        condition = self.get_object()
        application = m.LogisticsApplication.objects.select_for_update().get(pk=condition.application_id)
        assert_reviewer(request.user, application)
        services.assert_state(application, services.REVIEWABLE)
        payload = s.ResolutionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        documents = condition.documents.filter(is_current=True)
        if condition.cleared_at or not documents.exists() or documents.exclude(status='verified').exists() or documents.filter(expires_on__lt=timezone.localdate()).exists():
            raise ConflictError('An outstanding condition requires current verified evidence before clearance.')
        condition.cleared_at, condition.cleared_by = timezone.now(), request.user
        condition.save(update_fields=['cleared_at', 'cleared_by', 'updated_at'])
        services.audit(request, application.company, 'condition_cleared', condition_id=str(condition.pk), notes=payload.validated_data['notes'])
        return Response(self.get_serializer(condition).data)


class RestrictionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.ScopeRestriction.objects.none()
    serializer_class = s.RestrictionSerializer
    filterset_fields = ['company', 'service_scope', 'automatic']

    def get_queryset(self):
        return m.ScopeRestriction.objects.filter(company_id__in=company_ids(self.request.user))

    def perform_create(self, serializer):
        company = serializer.validated_data['company']
        if not can_review(self.request.user, company):
            raise PermissionDenied('Only authorised reviewers can apply restrictions.')
        restriction = serializer.save(applied_by=self.request.user)
        services.audit(self.request, company, 'restriction_applied', restriction_id=str(restriction.pk))
        services.notify(company, 'Service scope restricted', restriction.service_scope)

    @extend_schema(request=s.ResolutionSerializer, responses=s.RestrictionSerializer)
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        restriction = self.get_object()
        if not can_review(request.user, restriction.company):
            raise PermissionDenied('Only authorised reviewers may resolve restrictions.')
        if restriction.resolved_at:
            raise ConflictError('This restriction is already resolved.')
        payload = s.ResolutionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        if restriction.source_condition and not restriction.source_condition.cleared_at:
            raise ConflictError('Clear the source condition first.')
        if restriction.source_document:
            source = restriction.source_document
            current = m.LogisticsDocument.objects.filter(application=source.application, document_type=source.document_type, is_current=True).first()
            if not current or current.status != 'verified' or (current.expires_on and current.expires_on < timezone.localdate()):
                raise ConflictError('A current verified replacement credential is required.')
        restriction.resolved_at, restriction.resolved_by = timezone.now(), request.user
        restriction.resolution_notes = payload.validated_data['notes']
        restriction.save(update_fields=['resolved_at', 'resolved_by', 'resolution_notes', 'updated_at'])
        services.audit(request, restriction.company, 'restriction_resolved', restriction_id=str(restriction.pk))
        return Response(self.get_serializer(restriction).data)


class NotificationViewSet(mixins.ListModelMixin, AtomicViewSet):
    queryset = m.Notification.objects.none()
    serializer_class = s.NotificationSerializer

    def get_queryset(self):
        return m.Notification.objects.filter(recipient=self.request.user, company_id__in=company_ids(self.request.user))

    @extend_schema(request=None, responses=s.NotificationSerializer)
    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=['read_at'])
        return Response(self.get_serializer(notification).data)


class AlertViewSet(mixins.ListModelMixin, AtomicViewSet):
    queryset = m.MonitoringEvent.objects.none()
    serializer_class = s.AlertSerializer
    filterset_fields = ['company', 'kind']

    def get_queryset(self):
        return m.MonitoringEvent.objects.filter(company_id__in=company_ids(self.request.user)).order_by('-created_at')


def audit_rows(company_id):
    # Do not expose internal notes or private evidence content through audit metadata.
    return list(AccountAuditEvent.objects.filter(event_type__startswith='logistics.', metadata__company_id=str(company_id)).order_by('-created_at').values('id', 'event_type', 'created_at', 'actor_id')[:200])


class SummaryViewSet(AtomicViewSet):
    serializer_class = s.SummarySerializer

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=['get'])
    def me(self, request):
        companies = m.LogisticsCompany.objects.filter(pk__in=company_ids(request.user))
        return Response({'is_staff': request.user.is_staff, 'companies': [
            {'id': company.pk, 'name': company.organisation.name, 'can_edit': can_edit(request.user, company),
             'can_review': can_review(request.user, company), 'can_read': True} for company in companies]})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        companies = m.LogisticsCompany.objects.filter(pk__in=company_ids(request.user)).select_related('organisation')
        data = []
        totals = {
            'fleet_count': 0,
            'driver_count': 0,
            'open_requests': 0,
            'expiring_documents': 0,
            'open_alerts': 0,
            'restricted_scopes': 0,
        }
        for company in companies:
            application = m.LogisticsApplication.objects.filter(company=company).first()
            restricted = set(company.restrictions.filter(resolved_at__isnull=True).values_list('service_scope', flat=True))
            permitted = [scope for scope in company.services if scope not in restricted] if application and application.status in {'approved', 'conditionally_approved'} else []
            expiring_documents = company.application.documents.filter(is_current=True, expires_on__lte=timezone.localdate() + timedelta(days=30)).count() if application else 0
            open_alerts = company.monitoringevent_set.exclude(kind='resolved').count()
            fleet_count = company.vehicles.filter(is_active=True).count()
            driver_count = company.drivers.filter(is_active=True).count()
            open_requests = application.requests.exclude(status='accepted').count() if application else 0
            totals['fleet_count'] += fleet_count
            totals['driver_count'] += driver_count
            totals['open_requests'] += open_requests
            totals['expiring_documents'] += expiring_documents
            totals['open_alerts'] += open_alerts
            totals['restricted_scopes'] += len(restricted)
            data.append({'company_id': company.pk, 'reference': company.reference, 'name': company.organisation.name,
                         'application_id': application.pk if application else None,
                         'status': application.status if application else 'not_started',
                         'progress': services.progress(application) if application else None,
                         'risk': services.risk(application) if application else None,
                         'fleet_count': fleet_count,
                         'driver_count': driver_count,
                         'open_requests': open_requests,
                         'expiring_documents': expiring_documents,
                         'open_alerts': open_alerts,
                         'permitted_scopes': permitted, 'restricted_scopes': sorted(restricted)})
        return Response({'companies': data, 'company_count': len(data),
                         'totals': totals,
                         'unread_notifications': m.Notification.objects.filter(recipient=request.user, company__in=companies, read_at__isnull=True).count()})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=['get'])
    def risk(self, request):
        applications = m.LogisticsApplication.objects.filter(company_id__in=company_ids(request.user))
        rows = [{'company_id': app.company_id, 'application_id': app.pk, **services.risk(app)} for app in applications]
        return Response({'applications': sorted(rows, key=lambda row: row['compliance_score'])})

    @extend_schema(responses=s.SummarySerializer)
    @action(detail=False, methods=['get'])
    def audit(self, request):
        rows = []
        for pk in company_ids(request.user):
            rows.extend(audit_rows(pk))
        return Response({'events': sorted(rows, key=lambda row: row['created_at'], reverse=True)[:200]})


class ReportViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, AtomicViewSet):
    queryset = m.LogisticsReport.objects.none()
    serializer_class = s.ReportSerializer

    def get_queryset(self):
        return m.LogisticsReport.objects.filter(requested_by=self.request.user)

    @extend_schema(request=None, responses={201: s.ReportSerializer})
    def create(self, request, *args, **kwargs):
        companies = m.LogisticsCompany.objects.filter(pk__in=company_ids(request.user)).select_related('organisation')
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Reference', 'Company', 'Application status', 'Compliance score', 'Risk band', 'Active vehicles', 'Active drivers'])
        ids = []
        for company in companies:
            ids.append(str(company.pk))
            application = m.LogisticsApplication.objects.filter(company=company).first()
            score = services.risk(application) if application else {}
            # Prevent spreadsheet formula execution in exported user-supplied cells.
            name = company.organisation.name
            if name.lstrip().startswith(('=', '+', '-', '@')):
                name = "'" + name
            writer.writerow([company.reference, name, application.status if application else 'not_started', score.get('compliance_score', ''), score.get('risk_band', ''), company.vehicles.filter(is_active=True).count(), company.drivers.filter(is_active=True).count()])
            services.audit(request, company, 'register_exported')
        report = m.LogisticsReport.objects.create(requested_by=request.user, company_ids=ids, content=output.getvalue())
        return Response(self.get_serializer(report).data, status=201)

    @extend_schema(responses=OpenApiTypes.BINARY)
    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        report = self.get_object()
        if not set(report.company_ids).issubset({str(pk) for pk in company_ids(request.user)}):
            raise PermissionDenied('Your access to this report has changed; generate a new report.')
        response = HttpResponse(report.content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="logistics-register-{report.pk}.csv"'
        response['Cache-Control'] = 'private, no-store'
        return response
