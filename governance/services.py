"""State changes for governance records.

Every workflow step goes through one function here, which checks the current
state, checks the caller's role, makes the change and writes the audit entry.
The views wrap each request in a transaction, so the change and its audit
record land together or not at all.
"""
import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.audit import record_account_event
from common.exceptions import ConflictError
from governance import models as m
from governance import permissions as perms

P = m.PolicyStatus


def audit(request, event, **metadata):
    record_account_event(request, "governance." + event, **{k: str(v) for k, v in metadata.items()})


def display_name(user):
    if user is None:
        return ""
    return getattr(user, "full_name", "") or user.email


# Policies -----------------------------------------------------------------

POLICY_TEXT_FIELDS = [
    "title",
    "domain",
    "sector",
    "responsible_organisation",
    "jurisdiction",
    "review_date",
    "executive_summary",
    "strategic_context",
    "objectives",
    "stakeholders",
    "regulatory_instruments",
    "expected_outcomes",
]

EDITABLE_POLICY_STATES = {P.DRAFT, P.REVISION_REQUESTED}

# action: (capability, states it may start from, state it ends in, comment required)
POLICY_TRANSITIONS = {
    "submit": ("author", {P.DRAFT, P.REVISION_REQUESTED}, P.SUBMITTED, False),
    "start_review": ("review", {P.SUBMITTED}, P.UNDER_REVIEW, False),
    "request_revision": ("review", {P.SUBMITTED, P.UNDER_REVIEW, P.RECOMMENDED}, P.REVISION_REQUESTED, True),
    "recommend": ("review", {P.UNDER_REVIEW}, P.RECOMMENDED, False),
    "approve": ("approve", {P.UNDER_REVIEW, P.RECOMMENDED}, P.APPROVED, False),
    "reject": ("approve", {P.SUBMITTED, P.UNDER_REVIEW, P.RECOMMENDED}, P.REJECTED, True),
    "publish": ("approve", {P.APPROVED}, P.PUBLISHED, False),
    "archive": ("approve", {P.APPROVED, P.PUBLISHED, P.REJECTED}, P.ARCHIVED, True),
}


def snapshot(policy):
    data = {}
    for field in POLICY_TEXT_FIELDS:
        value = getattr(policy, field)
        data[field] = value.isoformat() if hasattr(value, "isoformat") else value
    data["owner"] = str(policy.owner_id)
    return data


def assert_policy_editable(policy):
    if policy.status not in EDITABLE_POLICY_STATES:
        raise ConflictError(
            "This policy can only be edited while it is a draft or has a revision requested.",
            code="policy_not_editable",
        )


def allowed_policy_actions(user, policy):
    """The workflow actions this caller may take on this policy right now."""
    actions = []
    for action, (capability, sources, _target, _comment) in POLICY_TRANSITIONS.items():
        if policy.status not in sources or not perms.has(user, capability):
            continue
        if action == "approve" and policy.created_by_id == user.pk:
            continue
        actions.append(action)
    return actions


def transition_policy(request, policy, action, comment=""):
    if action not in POLICY_TRANSITIONS:
        raise ValidationError({"action": f"Unknown action {action}."})
    capability, sources, target, comment_required = POLICY_TRANSITIONS[action]
    perms.require(request.user, capability)
    if policy.status not in sources:
        raise ConflictError(
            f"A policy that is {policy.get_status_display().lower()} cannot be moved this way.",
            code="invalid_policy_state",
        )
    comment = (comment or "").strip()
    if comment_required and not comment:
        raise ValidationError({"comment": "Give a reason for this decision."})
    if action == "approve" and policy.created_by_id == request.user.pk:
        raise PermissionDenied("You cannot approve a policy you wrote. Another executive must approve it.")

    previous = policy.status
    now = timezone.now()
    if action == "submit":
        # A resubmission after a revision is a new minor version.
        if previous == P.REVISION_REQUESTED:
            policy.version_minor += 1
    elif action == "approve":
        policy.approved_by = request.user
        policy.approved_at = now
    elif action == "publish":
        policy.version_major += 1
        policy.version_minor = 0
        policy.published_at = now
    policy.status = target
    policy.save()

    m.PolicyEvent.objects.create(
        policy=policy,
        action=action,
        from_status=previous,
        to_status=target,
        version=policy.version,
        comment=comment,
        snapshot=snapshot(policy),
        actor=request.user,
    )
    audit(request, "policy_" + action, policy_id=policy.pk, reference=policy.reference, from_status=previous, to_status=target)
    return policy


def record_policy_created(request, policy):
    m.PolicyEvent.objects.create(
        policy=policy,
        action="create",
        from_status="",
        to_status=policy.status,
        version=policy.version,
        snapshot=snapshot(policy),
        actor=request.user,
    )
    audit(request, "policy_created", policy_id=policy.pk, reference=policy.reference)


# Risks --------------------------------------------------------------------

R = m.RiskStatus


def escalate_risk(request, risk, comment):
    perms.require(request.user, "escalate")
    if risk.status in {R.ESCALATED, R.CLOSED}:
        raise ConflictError(f"This risk is already {risk.get_status_display().lower()}.", code="invalid_risk_state")
    if not (comment or "").strip():
        raise ValidationError({"comment": "Say why this risk is being escalated."})
    previous = risk.status
    risk.status = R.ESCALATED
    risk.escalated_at = timezone.now()
    risk.save()
    audit(request, "risk_escalated", risk_id=risk.pk, reference=risk.reference, from_status=previous, comment=comment.strip())
    return risk


def set_risk_status(request, risk, status, comment=""):
    perms.require(request.user, "escalate")
    if status not in {R.OPEN, R.MONITORING, R.CLOSED}:
        raise ValidationError({"status": "Use escalate to escalate a risk."})
    if risk.status == status:
        raise ConflictError("The risk already has that status.", code="invalid_risk_state")
    if status == R.CLOSED and not (comment or "").strip():
        raise ValidationError({"comment": "Record why the risk is being closed."})
    previous = risk.status
    risk.status = status
    risk.closed_at = timezone.now() if status == R.CLOSED else None
    risk.save()
    audit(request, "risk_status_changed", risk_id=risk.pk, reference=risk.reference, from_status=previous, to_status=status, comment=(comment or "").strip())
    return risk


# Regulatory findings ------------------------------------------------------

F = m.FindingStatus


def verify_finding(request, finding, comment=""):
    perms.require(request.user, "review")
    if finding.status != F.PENDING:
        raise ConflictError("Only a finding pending verification can be verified.", code="invalid_finding_state")
    if finding.recorded_by_id == request.user.pk:
        raise PermissionDenied("A finding must be verified by someone other than the person who recorded it.")
    finding.status = F.VERIFIED
    finding.verified_by = request.user
    finding.verified_at = timezone.now()
    finding.save()
    audit(request, "finding_verified", finding_id=finding.pk, reference=finding.reference, comment=(comment or "").strip())
    return finding


def escalate_finding(request, finding, comment):
    if not (perms.has(request.user, "review") or perms.has(request.user, "author")):
        raise PermissionDenied("Your governance role does not allow this action.")
    if finding.status in {F.ESCALATED, F.CLOSED}:
        raise ConflictError(f"This finding is already {finding.get_status_display().lower()}.", code="invalid_finding_state")
    if not (comment or "").strip():
        raise ValidationError({"comment": "Say why this finding is being escalated."})
    previous = finding.status
    finding.status = F.ESCALATED
    finding.save()
    audit(request, "finding_escalated", finding_id=finding.pk, reference=finding.reference, from_status=previous, comment=comment.strip())
    return finding


def close_finding(request, finding, resolution):
    perms.require(request.user, "review")
    if finding.status == F.CLOSED:
        raise ConflictError("This finding is already closed.", code="invalid_finding_state")
    if finding.status == F.PENDING:
        raise ConflictError("Verify the finding before closing it.", code="invalid_finding_state")
    if not (resolution or "").strip():
        raise ValidationError({"resolution": "Record how the gap was resolved."})
    previous = finding.status
    finding.status = F.CLOSED
    finding.resolution = resolution.strip()
    finding.save()
    audit(request, "finding_closed", finding_id=finding.pk, reference=finding.reference, from_status=previous)
    return finding


# Access -------------------------------------------------------------------

S = m.MemberStatus


def new_invitation_token():
    return secrets.token_urlsafe(32)


def invitation_by_token(token, *, lock=False):
    queryset = m.GovernanceInvitation.objects
    if lock:
        queryset = queryset.select_for_update()
    return queryset.filter(token_hash=m.hash_token(token or "")).first()


def refresh_expiry(invitation):
    if invitation.status == m.InvitationStatus.PENDING and invitation.is_expired:
        invitation.status = m.InvitationStatus.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
    return invitation


def assert_not_already_in(email):
    from accounts.models import User

    email = email.strip().lower()
    existing = User.objects.filter(email__iexact=email).first()
    if existing and m.GovernanceMember.objects.filter(user=existing, status__in=[S.ACTIVE, S.PENDING, S.SUSPENDED]).exists():
        raise ConflictError("This person already has governance access or a request in progress.", code="already_member")
    if m.GovernanceInvitation.objects.filter(email__iexact=email, status=m.InvitationStatus.PENDING, expires_at__gt=timezone.now()).exists():
        raise ConflictError("This email already has a pending invitation.", code="invitation_pending")


def create_invitation(request, *, email, name, organisation, role):
    perms.require(request.user, "manage_access")
    email = email.strip().lower()
    assert_not_already_in(email)
    token = new_invitation_token()
    invitation = m.GovernanceInvitation.objects.create(
        email=email,
        name=name.strip(),
        organisation=organisation.strip(),
        role=role,
        token_hash=m.hash_token(token),
        invited_by=request.user,
    )
    audit(request, "invitation_created", invitation_id=invitation.pk, email=email, role=role)
    send_invitation(request, invitation, token)
    return invitation, token


def send_invitation(request, invitation, token):
    from accounts.services import enqueue_account_email

    inviter = display_name(request.user)
    role_label = m.GovernanceRole(invitation.role).label
    transaction.on_commit(
        lambda: enqueue_account_email(
            "send_governance_invitation_email",
            invitation.email,
            invitation.name,
            inviter,
            role_label,
            invitation.organisation,
            token,
        )
    )


def resend_invitation(request, invitation):
    perms.require(request.user, "manage_access")
    refresh_expiry(invitation)
    if invitation.status != m.InvitationStatus.PENDING:
        raise ConflictError("Only a pending invitation can be resent.", code="invitation_not_pending")
    # A resend issues a fresh token, so an old forwarded link stops working.
    token = new_invitation_token()
    invitation.token_hash = m.hash_token(token)
    invitation.sent_count += 1
    invitation.last_sent_at = timezone.now()
    invitation.expires_at = m.invitation_expiry()
    invitation.save()
    audit(request, "invitation_resent", invitation_id=invitation.pk, email=invitation.email)
    send_invitation(request, invitation, token)
    return invitation, token


def revoke_invitation(request, invitation):
    perms.require(request.user, "manage_access")
    refresh_expiry(invitation)
    if invitation.status != m.InvitationStatus.PENDING:
        raise ConflictError("Only a pending invitation can be revoked.", code="invitation_not_pending")
    invitation.status = m.InvitationStatus.REVOKED
    invitation.save()
    audit(request, "invitation_revoked", invitation_id=invitation.pk, email=invitation.email)
    return invitation


def accept_invitation(request, token):
    user = request.user
    invitation = invitation_by_token(token, lock=True)
    if invitation is None:
        raise ConflictError("This invitation link is not valid.", code="invitation_invalid")
    refresh_expiry(invitation)
    if invitation.status != m.InvitationStatus.PENDING:
        raise ConflictError(f"This invitation is {invitation.get_status_display().lower()}.", code="invitation_not_pending")
    if invitation.email.lower() != user.email.lower():
        raise PermissionDenied("Sign in with the email address this invitation was sent to.")
    member = m.GovernanceMember.objects.select_for_update().filter(user=user).first()
    if member and member.status == S.SUSPENDED:
        raise PermissionDenied("Your governance access is suspended. Ask a Super Administrator to restore it.")
    now = timezone.now()
    if member is None:
        member = m.GovernanceMember(user=user)
    member.role = invitation.role
    member.status = S.ACTIVE
    member.organisation = invitation.organisation
    member.invited_by = invitation.invited_by
    member.decided_by = invitation.invited_by
    member.decided_at = now
    member.save()
    invitation.status = m.InvitationStatus.ACCEPTED
    invitation.accepted_by = user
    invitation.accepted_at = now
    invitation.save()
    audit(request, "invitation_accepted", invitation_id=invitation.pk, member_id=member.pk, role=member.role)
    return member


def request_access(request, *, organisation, job_title, profile, scope, requested_role):
    user = request.user
    existing = m.GovernanceMember.objects.select_for_update().filter(user=user).first()
    if existing and existing.status in {S.ACTIVE, S.PENDING, S.SUSPENDED}:
        raise ConflictError("You already have governance access or a request in progress.", code="already_member")
    # Privileged roles are granted only by invitation; a public request may
    # ask for at most an analyst seat, and the administrator decides.
    if requested_role not in {m.GovernanceRole.ANALYST, m.GovernanceRole.REVIEWER, m.GovernanceRole.VIEWER}:
        raise ValidationError({"requested_role": "Request an analyst, reviewer or viewer seat. Other roles are by invitation."})
    member = existing or m.GovernanceMember(user=user)
    member.role = requested_role
    member.status = S.PENDING
    member.organisation = organisation.strip()
    member.job_title = job_title.strip()
    member.profile = profile
    member.scope = scope
    member.decided_by = None
    member.decided_at = None
    member.decision_notes = ""
    member.save()
    audit(request, "access_requested", member_id=member.pk, role=requested_role)
    return member


def decide_member(request, member, *, status, role=None, notes=""):
    perms.require(request.user, "manage_access")
    if member.user_id == request.user.pk:
        raise PermissionDenied("You cannot change your own governance access.")
    allowed = {
        S.PENDING: {S.ACTIVE, S.DECLINED},
        S.ACTIVE: {S.SUSPENDED},
        S.SUSPENDED: {S.ACTIVE},
        S.DECLINED: set(),
    }
    if status not in allowed[member.status] and not (role and status == member.status == S.ACTIVE):
        raise ConflictError(
            f"An account that is {member.get_status_display().lower()} cannot be set to {S(status).label.lower()}.",
            code="invalid_member_state",
        )
    if status == S.DECLINED and not (notes or "").strip():
        raise ValidationError({"notes": "Give a reason for declining the request."})
    previous_status, previous_role = member.status, member.role
    member.status = status
    if role:
        member.role = role
    member.decided_by = request.user
    member.decided_at = timezone.now()
    member.decision_notes = (notes or "").strip()
    member.save()
    audit(
        request,
        "member_updated",
        member_id=member.pk,
        email=member.user.email,
        from_status=previous_status,
        to_status=member.status,
        from_role=previous_role,
        to_role=member.role,
    )
    return member
