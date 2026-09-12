import hashlib
import secrets
import uuid

from django.utils import timezone

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
        role = _ORG_TYPE_TO_ROLE.get(membership.organisation.organisation_type)
        if role:
            return role

    from marketplace.models import MarketplaceOrder

    if MarketplaceOrder.objects.filter(buyer=user).exists():
        return "buyer"

    return None


def can_review(user):
    """Reviewers vet applications/samples: staff, or partner/regulator org members."""
    role = derive_role(user)
    return bool(user and user.is_active and (user.is_staff or role in {"partner", "regulator", "operator"}))


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
