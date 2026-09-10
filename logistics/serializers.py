from pathlib import Path

from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field
from drf_spectacular.types import OpenApiTypes

from logistics import models as m
from logistics.permissions import can_see_driver_details


def strings(value):
    if not isinstance(value, list) or not value or len(value) > 100:
        raise serializers.ValidationError('Supply between 1 and 100 values.')
    if any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in value):
        raise serializers.ValidationError('Values must be nonblank strings of at most 100 characters.')
    if len({x.strip().casefold() for x in value}) != len(value):
        raise serializers.ValidationError('Duplicate values are not allowed.')
    return [x.strip() for x in value]


def future_date(value):
    if value < timezone.localdate():
        raise serializers.ValidationError('The deadline cannot be in the past.')
    return value


def validity(dates):
    days = [(date - timezone.localdate()).days for date in dates if date]
    if any(day < 0 for day in days):
        return 'expired'
    return 'expiring' if any(day <= 30 for day in days) else 'current'


def validate_file(value):
    allowed = {
        '.pdf': {'application/pdf'}, '.doc': {'application/msword'},
        '.docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'},
        '.jpg': {'image/jpeg'}, '.jpeg': {'image/jpeg'}, '.png': {'image/png'},
        '.xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        '.zip': {'application/zip', 'application/x-zip-compressed'},
    }
    suffix = Path(value.name).suffix.lower()
    if suffix not in allowed or value.content_type not in allowed[suffix]:
        raise serializers.ValidationError('Use PDF, Word, JPEG, PNG, XLSX or ZIP with the matching content type.')
    if not 0 < value.size <= 20 * 1024 * 1024:
        raise serializers.ValidationError('Files must be nonempty and no larger than 20 MB.')
    return value


class OwnedSerializer(serializers.ModelSerializer):
    def validate_company(self, value):
        if self.instance and value.pk != self.instance.company_id:
            raise serializers.ValidationError('The company cannot be changed.')
        return value


class CompanySerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='organisation.name', read_only=True)
    registration_number = serializers.CharField(source='organisation.registration_number', read_only=True)
    services = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)

    class Meta:
        model = m.LogisticsCompany
        fields = ['id', 'organisation', 'reference', 'name', 'registration_number', 'contact_name', 'contact_email', 'contact_phone', 'incorporated_on', 'employees', 'annual_tonnage', 'services', 'created_at', 'updated_at']
        read_only_fields = ['id', 'reference', 'created_at', 'updated_at']

    def validate_services(self, value):
        return strings(value)

    def validate_organisation(self, value):
        if self.instance and value.pk != self.instance.organisation_id:
            raise serializers.ValidationError('The organisation cannot be changed.')
        return value


class LocationSerializer(OwnedSerializer):
    class Meta:
        model = m.OperatingLocation
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class VehicleSerializer(OwnedSerializer):
    credential_status = serializers.SerializerMethodField()

    class Meta:
        model = m.Vehicle
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_credential_status(self, obj) -> str:
        return validity([obj.insurance_expiry, obj.roadworthiness_expiry])

    def validate_year(self, value):
        if value > timezone.localdate().year + 1:
            raise serializers.ValidationError('Vehicle year cannot be more than one year ahead.')
        return value


class DriverSerializer(OwnedSerializer):
    credential_status = serializers.SerializerMethodField()
    training = serializers.ListField(child=serializers.CharField(max_length=100), required=False)

    class Meta:
        model = m.Driver
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        company = attrs.get('company', getattr(self.instance, 'company', None))
        vehicle = attrs.get('assigned_vehicle', getattr(self.instance, 'assigned_vehicle', None))
        if vehicle and (vehicle.company_id != company.pk or not vehicle.is_active):
            raise serializers.ValidationError({'assigned_vehicle': 'Choose an active vehicle belonging to this company.'})
        return attrs

    def get_credential_status(self, obj) -> str:
        return validity([obj.licence_expiry, obj.medical_expiry])

    def to_representation(self, obj):
        result = super().to_representation(obj)
        if not can_see_driver_details(self.context['request'].user, obj.company):
            for field in ['national_id', 'medical_expiry', 'licence_number']:
                result.pop(field, None)
        return result


class GrantSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.LogisticsAccessGrant
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class DomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.DomainReview
        fields = ['key', 'data', 'applicable', 'status', 'score', 'review_notes', 'reviewed_at']
        read_only_fields = fields


class SectionInputSerializer(serializers.Serializer):
    data = serializers.DictField(allow_empty=False)


class DomainReviewInputSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['passed', 'attention', 'failed'])
    score = serializers.IntegerField(min_value=0, max_value=100)
    notes = serializers.CharField()
    applicable = serializers.BooleanField(default=True)


class ConditionSerializer(serializers.ModelSerializer):
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = m.ApprovalCondition
        fields = ['id', 'title', 'description', 'due_date', 'service_scope', 'cleared_at', 'is_overdue']
        read_only_fields = ['id', 'cleared_at', 'is_overdue']

    def validate_due_date(self, value):
        return future_date(value)

    def get_is_overdue(self, obj) -> bool:
        return not obj.cleared_at and obj.due_date < timezone.localdate()


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.LogisticsDocument
        fields = ['domain', 'document_type', 'title', 'issuer', 'reference', 'issued_on', 'expires_on', 'service_scope', 'vehicle', 'driver', 'condition', 'file']

    def validate_file(self, value):
        return validate_file(value)

    def validate(self, attrs):
        application = self.context['application']
        for field in ['driver', 'vehicle']:
            obj = attrs.get(field)
            if obj and obj.company_id != application.company_id:
                raise serializers.ValidationError({field: 'This record belongs to another company.'})
        condition = attrs.get('condition')
        if condition and (condition.application_id != application.pk or condition.cleared_at):
            raise serializers.ValidationError({'condition': 'Choose an outstanding condition for this application.'})
        if attrs.get('service_scope') and attrs['service_scope'] not in application.company.services:
            raise serializers.ValidationError({'service_scope': 'Select a declared company service.'})
        if attrs.get('issued_on') and attrs.get('expires_on') and attrs['expires_on'] < attrs['issued_on']:
            raise serializers.ValidationError({'expires_on': 'Expiry cannot precede issue date.'})
        return attrs


class ConditionEvidenceUploadSerializer(DocumentUploadSerializer):
    class Meta:
        model = m.LogisticsDocument
        fields = ['domain', 'document_type', 'title', 'issuer', 'reference', 'issued_on', 'expires_on', 'file']


class DocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    validity = serializers.SerializerMethodField()

    class Meta:
        model = m.LogisticsDocument
        exclude = ['file']
        read_only_fields = [field.name for field in m.LogisticsDocument._meta.fields]

    def get_download_url(self, obj) -> str:
        return self.context['request'].build_absolute_uri(reverse('logistics-document-download', args=[obj.pk]))

    def get_validity(self, obj) -> str:
        return validity([obj.expires_on])


class ApplicationSerializer(OwnedSerializer):
    sections = DomainSerializer(many=True, read_only=True)
    conditions = ConditionSerializer(many=True, read_only=True)
    progress = serializers.SerializerMethodField()
    risk = serializers.SerializerMethodField()

    class Meta:
        model = m.LogisticsApplication
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'reviewer', 'status', 'submitted_at', 'reviewed_at', 'rationale', 'policy_version', 'domain_weights']

    def get_progress(self, obj) -> dict:
        from logistics.services import progress
        return progress(obj)

    def get_risk(self, obj) -> dict:
        from logistics.services import risk
        return risk(obj)


class AssignReviewerSerializer(serializers.Serializer):
    reviewer = serializers.PrimaryKeyRelatedField(queryset=m.LogisticsApplication._meta.get_field('reviewer').remote_field.model.objects.filter(is_active=True))


class ReviewDocumentInputSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['verified', 'rejected'])
    notes = serializers.CharField()


class DecisionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=['approved', 'conditionally_approved', 'rejected'])
    rationale = serializers.CharField()
    conditions = ConditionSerializer(many=True, required=False)

    def validate(self, attrs):
        if attrs.get('conditions') and attrs['status'] != 'conditionally_approved':
            raise serializers.ValidationError('Conditions may only accompany conditional approval.')
        return attrs


class NoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.DocumentNote
        fields = ['id', 'body', 'internal', 'author', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class RequestResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.RequestResponse
        fields = ['id', 'message', 'documents', 'author', 'created_at']
        read_only_fields = ['id', 'author', 'created_at']


class InformationRequestSerializer(serializers.ModelSerializer):
    responses = RequestResponseSerializer(many=True, read_only=True)
    is_overdue = serializers.SerializerMethodField()
    items = serializers.ListField(child=serializers.CharField(max_length=100), allow_empty=False)

    class Meta:
        model = m.InformationRequest
        fields = ['id', 'application', 'reason', 'message', 'items', 'due_date', 'status', 'raised_by', 'review_notes', 'responses', 'is_overdue', 'created_at']
        read_only_fields = ['id', 'application', 'status', 'raised_by', 'review_notes', 'created_at']

    def validate_items(self, value):
        return strings(value)

    def validate_due_date(self, value):
        return future_date(value)

    def get_is_overdue(self, obj) -> bool:
        return obj.status != 'accepted' and obj.due_date < timezone.localdate()


class ResponseReviewSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    notes = serializers.CharField()


class RestrictionSerializer(OwnedSerializer):
    class Meta:
        model = m.ScopeRestriction
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'automatic', 'source_document', 'source_condition', 'applied_by', 'resolved_at', 'resolved_by', 'resolution_notes']

    def validate(self, attrs):
        if attrs['service_scope'] not in attrs['company'].services:
            raise serializers.ValidationError({'service_scope': 'Select a declared service.'})
        return attrs


class ResolutionSerializer(serializers.Serializer):
    notes = serializers.CharField()


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.Notification
        ref_name = 'LogisticsNotification'
        fields = ['id', 'company', 'title', 'body', 'read_at', 'created_at']
        read_only_fields = fields


class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.MonitoringEvent
        ref_name = 'LogisticsAlert'
        fields = '__all__'


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = m.LogisticsReport
        ref_name = 'LogisticsReport'
        fields = ['id', 'report_type', 'created_at']
        read_only_fields = fields


class SummarySerializer(serializers.Serializer):
    """Aggregate response; domain records have dedicated typed serializers."""
    data = serializers.DictField()
