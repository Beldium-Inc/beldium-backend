"""API for the Mining Compliance dashboard.

Read scope is decided from the caller's audience: the operator desk and the
regulator see the whole register, a mining company sees only its own sites.
Mirrors ``processing.views.ProcessingDashboardView`` by design — see
``mining.models`` for why the two verticals stay separate apps.
"""
from collections import OrderedDict
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from mining.models import (
    Application,
    EnvRecord,
    Inspection,
    LicenceDoc,
    MineSite,
    NonConformity,
    SafetyIncident,
    SiteStatus,
)
from mining.permissions import IsMiningParticipant, OPERATOR, REGULATOR, audience, capabilities, organisation_ids
from mining.serializers import ApplicationSerializer, DashboardSerializer, EnvRecordSerializer, ExpiringLicenceSerializer, SafetyIncidentSerializer

LICENCE_EXPIRY_WARNING_DAYS = 60
KPI_TREND_MONTHS = 6


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
