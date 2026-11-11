import hashlib
import secrets
import uuid

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from organisations.access import sector_allowed
from organisations.models import OrganisationMembership, OrganisationType

# --- role derivation ---------------------------------------------------------
#
# accounts.models.User carries no `role` field matching the frontend's
# QualityRole ("operator" | "partner" | "miner" | "buyer" | "regulator"). We
# derive it from the caller's active organisation membership, the same way
# other apps (marketplace, processing) distinguish internal staff from
# external organisation types rather than storing a duplicate role field:
#
#   - is_staff (Beldium internal reviewers/decision makers) -> "operator"
#   - organisation_type == mining_company                   -> "miner"
#   - organisation_type in {compliance_partner, laboratory,
#     inspection_body}                                       -> "partner"
#   - organisation_type == regulator                         -> "regulator"
#   - otherwise, if the user (or their org) has placed a marketplace order as
#     buyer, there is no dedicated "buyer" organisation type in the system,
#     so we treat marketplace buyer activity as the signal               -> "buyer"
#   - none of the above matches -> None (no quality role)
#
# A user can technically satisfy more than one signal (e.g. a staff member who
# also has an org membership); staff/operator takes priority since operators
# are Beldium's internal quality officers with the broadest capabilities.
_ORG_TYPE_TO_ROLE = {
    OrganisationType.MINING_COMPANY: "miner",
    OrganisationType.COMPLIANCE_PARTNER: "partner",
    OrganisationType.LABORATORY: "partner",
    OrganisationType.INSPECTION_BODY: "partner",
    OrganisationType.REGULATOR: "regulator",
}

BLOCKED_ORGANISATION_STATUSES = {"suspended", "rejected"}


def derive_role(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if user.is_staff:
        return "operator"

    membership = (
        OrganisationMembership.objects.filter(user=user, is_active=True)
        .select_related("organisation")
        .first()
    )
    if membership:
        organisation = membership.organisation
        # A suspended or rejected organisation loses its quality seat at once,
        # whatever its type.
        if organisation.verification_status in BLOCKED_ORGANISATION_STATUSES:
            return None
        role = _ORG_TYPE_TO_ROLE.get(organisation.organisation_type)
        if role in {"partner", "regulator"}:
            # organisation_type is chosen by the applicant, so a laboratory or
            # regulator claim counts only once the platform has verified it.
            if organisation.verification_status != "verified":
                return None
            # A partner or regulator vetted for another sector has no seat
            # here; a miner, who registers no sector, is unaffected.
            if not sector_allowed(organisation, "quality"):
                return None
        if role:
            return role

    from marketplace.models import MarketplaceOrder

    if MarketplaceOrder.objects.filter(buyer=user).exists():
        return "buyer"

    return None


def user_organisation(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    membership = (
        OrganisationMembership.objects.filter(user=user, is_active=True)
        .select_related("organisation")
        .first()
    )
    return membership.organisation if membership else None


_APPLICANT_TYPES = {
    OrganisationType.COMPLIANCE_PARTNER,
    OrganisationType.LABORATORY,
    OrganisationType.INSPECTION_BODY,
}


def applicant_organisation(user):
    """The partner type organisation the caller may apply and upload for.

    An applicant is not verified yet, so this does not ask for the partner
    role. It only asks that the caller is an active member of a laboratory,
    inspection body or compliance partner that is not suspended or rejected.
    """
    org = user_organisation(user)
    if org is None or org.organisation_type not in _APPLICANT_TYPES:
        return None
    if org.verification_status in BLOCKED_ORGANISATION_STATUSES:
        return None
    return org


def can_review(user):
    """Only Beldium quality operators take applications for review and record
    the quality verdict on a sample. A laboratory enters results but never
    judges its own work, and a regulator reads without changing anything."""
    return can_decide(user)


def can_decide(user):
    """Only Beldium operators (staff) make binding accreditation/certification decisions."""
    return bool(user and user.is_active and user.is_staff)


# --- misc helpers -------------------------------------------------------------

def new_id():
    return str(uuid.uuid4())


def actor_label(user):
    if not user:
        return "System"
    name = (getattr(user, "full_name", "") or "").strip()
    return name or user.email


def audit_entry(user, action, detail=""):
    return {
        "id": new_id(),
        "at": timezone.now().isoformat(),
        "actor": actor_label(user),
        "role": derive_role(user) or "",
        "action": action,
        "detail": detail,
    }


def gen_hash(*parts):
    seed = "|".join(str(p) for p in parts) + secrets.token_hex(8)
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


SAMPLE_TRANSITIONS = {
    "registered": {"in_transit", "rejected"},
    "in_transit": {"received", "rejected"},
    "received": {"testing", "rejected"},
    "testing": {"reviewed", "rejected"},
    "reviewed": {"certified", "rejected"},
    "certified": set(),
    "rejected": set(),
}


def transition_sample(sample, new_status):
    if new_status == sample.status:
        return
    allowed = SAMPLE_TRANSITIONS.get(sample.status, set())
    if new_status not in allowed:
        from common.exceptions import ConflictError
        raise ConflictError(f"Sample cannot move from {sample.status} to {new_status}.")
    sample.status = new_status


def require_role(user, *roles):
    role = derive_role(user)
    if role not in roles:
        raise PermissionDenied("You do not have permission to perform this action.")
    return role


def parse_number(value):
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def certificate_blockers(sample):
    """Every reason this sample may not be certified, empty when it may.

    Results are judged again here from their stored limits and values, so a
    verdict typed by hand or saved under an older rule can never let a failed
    or missing result through.
    """
    reasons = []
    review = sample.quality_review or {}
    if review.get("verdict") != "pass":
        reasons.append("The quality review verdict is not pass.")
    results = list(sample.results or [])
    if not results:
        reasons.append("The sample has no test results.")
    for row in results:
        name = row.get("analyte") or row.get("method") or "A result"
        verdict = limit_verdict(row.get("limit") or {}, row.get("value"))
        if verdict == "pending":
            reasons.append(f"{name} has no measured value.")
        elif verdict != "pass":
            reasons.append(f"{name} does not meet the specification.")
    return reasons


def limit_verdict(limit, measured):
    value = parse_number(measured)
    if value is None:
        return "pending"
    minimum = parse_number(limit.get("min"))
    maximum = parse_number(limit.get("max"))
    if minimum is not None and value < minimum:
        return "fail"
    if maximum is not None and value > maximum:
        return "fail"
    return "pass"


def result_rows_from_spec(spec, methods):
    limits = list(getattr(spec, "limits", None) or [])
    if limits:
        rows = []
        for limit in limits:
            method = limit.get("method") or (methods[0] if methods else "")
            rows.append({
                "id": new_id(),
                "analyte": limit.get("analyte", ""),
                "method": method,
                "value": "",
                "unit": limit.get("unit", ""),
                "spec": limit.get("spec") or limit.get("label") or _limit_label(limit),
                "limit": limit,
                "verdict": "pending",
                "uncertainty": limit.get("uncertainty", ""),
            })
        return rows
    return [
        {
            "id": new_id(), "analyte": method, "method": method, "value": "",
            "unit": "", "spec": "", "limit": {}, "verdict": "pending", "uncertainty": "",
        }
        for method in methods
    ]


def _limit_label(limit):
    parts = []
    if limit.get("min") not in {"", None}:
        parts.append(f">= {limit['min']}")
    if limit.get("max") not in {"", None}:
        parts.append(f"<= {limit['max']}")
    return " and ".join(parts)
