"""Serializers backing the Mining Compliance dashboard API."""
from rest_framework import serializers

from mining.models import Application, EnvRecord, LicenceDoc, SafetyIncident


class ApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Application
        fields = [
            "id",
            "reference",
            "organisation",
            "site",
            "site_name",
            "type",
            "mineral",
            "submitted_on",
            "stage",
            "status",
            "assigned_to",
            "sla_days",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "reference", "created_at", "updated_at"]


class EnvRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnvRecord
        fields = ["id", "site", "metric", "value", "limit", "status", "measured_on"]


class SafetyIncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = SafetyIncident
        fields = ["id", "site", "date", "type", "severity", "lost_days", "status", "summary"]


class ExpiringLicenceSerializer(serializers.ModelSerializer):
    """Register-wide licence/permit expiry watchlist row."""

    site_name = serializers.CharField(source="site.name", read_only=True)
    days_to_expiry = serializers.SerializerMethodField()

    class Meta:
        model = LicenceDoc
        fields = ["id", "number", "type", "site_name", "authority", "expires_on", "days_to_expiry", "status"]

    def get_days_to_expiry(self, obj) -> int | None:
        if not obj.expires_on:
            return None
        from django.utils import timezone

        return (obj.expires_on - timezone.localdate()).days


class DashboardTotalsSerializer(serializers.Serializer):
    sites = serializers.IntegerField()
    operational_sites = serializers.IntegerField()
    suspended_sites = serializers.IntegerField()
    applications = serializers.IntegerField()
    applications_pending = serializers.IntegerField()
    applications_under_review = serializers.IntegerField()
    open_non_conformities = serializers.IntegerField()
    overdue_non_conformities = serializers.IntegerField()
    upcoming_inspections = serializers.IntegerField()
    environmental_watch = serializers.IntegerField()
    environmental_breaches = serializers.IntegerField()
    open_safety_incidents = serializers.IntegerField()
    expiring_licences = serializers.IntegerField()
    average_compliance_score = serializers.IntegerField()


class DashboardSerializer(serializers.Serializer):
    audience = serializers.CharField()
    capabilities = serializers.DictField()
    totals = DashboardTotalsSerializer()
    kpi_trend = serializers.ListField()
    regional_compliance = serializers.ListField()
    expiring_licences = serializers.ListField()
    notifications = serializers.ListField()
    recent_applications = serializers.ListField()
    open_environmental_records = serializers.ListField()
    open_incidents = serializers.ListField()
