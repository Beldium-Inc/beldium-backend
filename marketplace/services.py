from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from marketplace import models as m

PRODUCT_CHECKS = [
    ("identity", "Seller and product identity"),
    ("origin", "Origin and chain of custody"),
    ("licensing", "Licensing and permit evidence"),
    ("quality", "Assay, grade or specification evidence"),
    ("trade", "Trade, price and fulfillment readiness"),
]


def audit(request, event_type, *, seller=None, product=None, order=None, **metadata):
    actor = getattr(request, "user", None)
    if actor and not actor.is_authenticated:
        actor = None
    return m.MarketplaceAuditEvent.objects.create(
        actor=actor,
        seller=seller,
        product=product,
        order=order,
        event_type="marketplace." + event_type,
        metadata=metadata,
    )


def notify(users, title, body, seller=None):
    unique = {user.pk: user for user in users if user and user.is_active}
    m.MarketplaceNotification.objects.bulk_create([
        m.MarketplaceNotification(recipient=user, seller=seller, title=title, body=body)
        for user in unique.values()
    ])


def seller_recipients(seller):
    from accounts.models import User

    return User.objects.filter(
        Q(is_staff=True)
        | Q(organisation_memberships__organisation=seller.organisation, organisation_memberships__is_active=True)
        | Q(marketplaceaccessgrant__seller=seller, marketplaceaccessgrant__is_active=True)
    ).distinct()


def initialise_product(product):
    m.ProductComplianceCheck.objects.bulk_create([
        m.ProductComplianceCheck(product=product, key=key, label=label)
        for key, label in PRODUCT_CHECKS
    ], ignore_conflicts=True)
    refresh_product_risk(product)


def valid_documents(product):
    return product.documents.filter(is_current=True).exclude(status=m.EvidenceStatus.REJECTED).filter(
        Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate())
    )


def product_progress(product):
    checks = list(product.checks.all())
    evidence_types = set(valid_documents(product).values_list("document_type", flat=True))
    required = {
        "identity": bool(product.seller.status in {m.SellerStatus.VERIFIED, m.SellerStatus.RESTRICTED}),
        "origin": "origin_certificate" in evidence_types or product.category in {m.ProductCategory.EQUIPMENT, m.ProductCategory.SERVICE},
        "licensing": "trade_license" in evidence_types or product.category == m.ProductCategory.SERVICE,
        "quality": "assay_report" in evidence_types or product.category in {m.ProductCategory.EQUIPMENT, m.ProductCategory.SERVICE},
        "trade": bool(Decimal(product.quantity_available) > 0 and Decimal(product.price) >= 0),
    }
    reviewed = {check.key: check.status for check in checks}
    return {
        "percent": round(sum(required.values()) / len(required) * 100),
        "checks": required,
        "missing": [key for key, ok in required.items() if not ok],
        "review_status": reviewed,
        "documents_current": product.documents.filter(is_current=True).count(),
    }


def refresh_product_risk(product):
    checks = list(product.checks.all())
    if checks:
        score = round(sum(check.score for check in checks) / len(checks))
    else:
        score = 0
    progress = product_progress(product) if product.pk else {"percent": 0}
    score = round((score + progress["percent"]) / 2)
    product.compliance_score = score
    product.risk_band = "low" if score >= 85 else "medium" if score >= 65 else "high"
    product.save(update_fields=["compliance_score", "risk_band", "updated_at"])
    return {"compliance_score": score, "risk_band": product.risk_band}


def require_listing_ready(product):
    progress = product_progress(product)
    if progress["percent"] != 100:
        from common.exceptions import ConflictError

        raise ConflictError("Complete marketplace product evidence before submission.", code="marketplace_listing_incomplete", details=progress)


def dashboard_for(sellers):
    sellers = list(sellers)
    products = m.MarketplaceProduct.objects.filter(seller__in=sellers)
    orders = m.MarketplaceOrder.objects.filter(seller__in=sellers)
    revenue = orders.filter(payment_status=m.PaymentStatus.PAID).aggregate(total=Sum("unit_price"))["total"] or 0
    return {
        "seller_count": len(sellers),
        "product_count": products.count(),
        "active_listings": products.filter(status=m.ListingStatus.ACTIVE).count(),
        "pending_reviews": products.filter(status=m.ListingStatus.PENDING_REVIEW).count(),
        "open_orders": orders.exclude(status__in=[m.OrderStatus.COMPLETED, m.OrderStatus.CANCELLED]).count(),
        "open_disputes": m.MarketplaceDispute.objects.filter(order__seller__in=sellers).exclude(status="resolved").count(),
        "paid_order_value": str(revenue),
    }
