"""Read only compliance intelligence drawn from every sector.

Governance users see the state of each sector's register; they never change
it. Each sector stores its records differently, so each gets a small reader
below that maps its own models onto one common set of measures:

``registered``     entities on the sector's register
``verified``       of those, the ones the sector desk has approved
``open_findings``  non-conformities, findings, incidents or disputes not yet closed
``expiring_30d``   certificates, licences and evidence expiring within 30 days
``open_requests``  information requests still waiting on the applicant

A measure a sector does not track is None, never zero, so the screen can say
"not tracked" instead of implying a clean record.

A sector whose reader fails is reported as unavailable and logged, so one
sector's schema change cannot take down the governance dashboard.
"""
import logging
from datetime import timedelta

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

EXPIRY_WINDOW_DAYS = 30

SECTORS = [
    ("mining", "Mining"),
    ("quality", "Quality & Control"),
    ("logistics", "Logistics"),
    ("warehousing", "Warehousing"),
    ("processing", "Processing"),
    ("export", "Exports"),
    ("marketplace", "Marketplace"),
]


def _model(label):
    return apps.get_model(label)


def _expiring(label, field, extra=None):
    today = timezone.localdate()
    window = today + timedelta(days=EXPIRY_WINDOW_DAYS)
    queryset = _model(label).objects.filter(**{f"{field}__gte": today, f"{field}__lte": window})
    if extra is not None:
        queryset = queryset.filter(extra)
    return queryset.count()


def _expiring_datetime(label, field, extra=None):
    now = timezone.now()
    queryset = _model(label).objects.filter(**{f"{field}__gte": now, f"{field}__lte": now + timedelta(days=EXPIRY_WINDOW_DAYS)})
    if extra is not None:
        queryset = queryset.filter(extra)
    return queryset.count()


def _count(label, **filters):
    return _model(label).objects.filter(**filters).count()


def _count_excluding(label, field, values):
    return _model(label).objects.exclude(**{f"{field}__in": values}).count()


APPROVED = ["approved", "conditionally_approved"]


def mining():
    return {
        "registered": _count("mining.MineSite"),
        "verified": _count("mining.MineSite", status="operational"),
        "open_findings": _count_excluding("mining.NonConformity", "status", ["closed"]),
        "expiring_30d": _expiring("mining.LicenceDoc", "expires_on", ~Q(status__in=["expired", "suspended"]))
        + _expiring("mining.DocumentRecord", "expires_on"),
        "open_requests": _count("mining.InfoRequest", status="open"),
    }


def quality():
    return {
        "registered": _count("quality.QualityApplication"),
        "verified": _count("quality.QualityApplication", status="approved"),
        "open_findings": _count_excluding("quality.QualityNonConformity", "status", ["closed"]),
        "expiring_30d": _expiring_datetime("quality.Certificate", "valid_until", Q(status="active")),
        "open_requests": _count("quality.QualityApplication", status="info_requested"),
    }


def logistics():
    return {
        "registered": _count("logistics.LogisticsApplication"),
        "verified": _count("logistics.LogisticsApplication", status__in=APPROVED),
        "open_findings": _count_excluding("logistics.ComplianceFinding", "status", ["cleared"]),
        "expiring_30d": _expiring("logistics.LogisticsDocument", "expires_on", ~Q(status="rejected"))
        + _expiring("logistics.Driver", "licence_expiry")
        + _expiring("logistics.Vehicle", "insurance_expiry"),
        "open_requests": _count("logistics.InformationRequest", status="open"),
    }


def warehousing():
    return {
        "registered": _count("warehousing.WarehousingApplication"),
        "verified": _count("warehousing.WarehousingApplication", status__in=APPROVED),
        "open_findings": _count_excluding("warehousing.Incident", "status", ["closed"]),
        "expiring_30d": _expiring("warehousing.Certificate", "expires_on", Q(status__in=["active", "conditional"]))
        + _expiring("warehousing.WarehousingDocument", "expires_on", ~Q(status="rejected")),
        "open_requests": _count("warehousing.InformationRequest", status="open"),
    }


def processing():
    return {
        "registered": _count("processing.Processor"),
        "verified": _count("processing.Processor", status__in=["approved", "conditional"]),
        "open_findings": _count_excluding("processing.NonConformity", "status", ["closed"]),
        "expiring_30d": _expiring("processing.ProcessingDocument", "expires_on"),
        "open_requests": None,
    }


def export():
    return {
        "registered": _count("export.ExportApplication"),
        "verified": _count("export.ExportApplication", status__in=APPROVED),
        "open_findings": _count_excluding("export.ShipmentNonConformity", "status", ["closed"]),
        "expiring_30d": _expiring("export.ExportDocument", "expires_on", ~Q(status="rejected"))
        + _expiring("export.Exporter", "license_expires_on"),
        "open_requests": _count("export.InformationRequest", status="open"),
    }


def marketplace():
    return {
        "registered": _count("marketplace.SellerProfile"),
        "verified": _count("marketplace.SellerProfile", status="verified"),
        "open_findings": _count_excluding("marketplace.MarketplaceDispute", "status", ["resolved"]),
        "expiring_30d": _expiring("marketplace.ProductDocument", "expires_on", ~Q(status="rejected"))
        + _expiring("marketplace.MarketplaceLicense", "expires_on", ~Q(status="rejected")),
        "open_requests": None,
    }


READERS = {
    "mining": mining,
    "quality": quality,
    "logistics": logistics,
    "warehousing": warehousing,
    "processing": processing,
    "export": export,
    "marketplace": marketplace,
}

SOURCES = {
    "mining": "Mine sites, licences, non-conformities and information requests",
    "quality": "Partner applications, certificates and non-conformities",
    "logistics": "Company applications, documents, drivers, vehicles and findings",
    "warehousing": "Facility applications, certificates, documents and incidents",
    "processing": "Processor register, documents and non-conformities",
    "export": "Exporter applications, documents, licences and shipment non-conformities",
    "marketplace": "Seller profiles, product evidence, licences and disputes",
}


def sector_summary():
    rows = []
    for slug, name in SECTORS:
        try:
            # A savepoint per sector, so a failed read cannot poison the
            # transaction the remaining sectors are read in.
            with transaction.atomic():
                metrics = READERS[slug]()
            available = True
        except Exception:  # noqa: BLE001 - one sector must not break the overview
            logger.exception("Governance could not read the %s register", slug)
            metrics = dict.fromkeys(["registered", "verified", "open_findings", "expiring_30d", "open_requests"])
            available = False
        registered = metrics["registered"]
        verified = metrics["verified"]
        rows.append(
            {
                "sector": slug,
                "name": name,
                "available": available,
                "source": SOURCES[slug],
                **metrics,
                "verified_rate": round(verified / registered * 100) if registered else None,
            }
        )
    return rows


def alerts(rows):
    """Plain statements drawn from the numbers, for the sectors that need attention."""
    found = []
    for row in rows:
        if not row["available"]:
            found.append({"sector": row["sector"], "name": row["name"], "level": "warning", "message": "This sector's register could not be read. Check the service logs."})
            continue
        if row["expiring_30d"]:
            found.append(
                {
                    "sector": row["sector"],
                    "name": row["name"],
                    "level": "warning",
                    "message": f"{row['expiring_30d']} certificate, licence or evidence record(s) expire within {EXPIRY_WINDOW_DAYS} days.",
                }
            )
        if row["open_findings"] and row["registered"] and row["open_findings"] >= row["registered"]:
            found.append(
                {
                    "sector": row["sector"],
                    "name": row["name"],
                    "level": "critical",
                    "message": f"{row['open_findings']} open findings against {row['registered']} registered entities: at least one open finding per entity.",
                }
            )
    return found
