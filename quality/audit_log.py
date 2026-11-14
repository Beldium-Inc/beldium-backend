"""The one write function for the Q&C audit log.

Call record() inside the same database transaction as the change it
describes, so the change and its audit row land together or not at all.
Refused calls are recorded after their transaction is rolled back, so the
refusal survives (see AtomicViewSet.dispatch in quality/views.py).
"""
from common.ip import client_ip
from quality import models as m


def _actor_fields(user):
    from quality import permissions

    if not user or not getattr(user, "is_authenticated", False):
        return {"actor_id": None, "actor_name": "", "actor_email": "", "actor_role_code": ""}
    name = (getattr(user, "full_name", "") or "").strip()
    return {
        "actor_id": user.pk,
        "actor_name": name or user.email,
        "actor_email": user.email,
        "actor_role_code": ",".join(sorted(permissions.role_codes(user))),
    }


def record(
    *, actor, action, record_type="", record_id="", record_label="", outcome=m.AuditOutcome.SUCCESS,
    reason="", before=None, after=None, request=None, rule_version="", batch_id="",
):
    return m.QualityAuditEntry.objects.create(
        **_actor_fields(actor),
        action=action[:120],
        record_type=record_type,
        record_id=str(record_id or "")[:64],
        record_label=str(record_label or "")[:120],
        batch_id=str(batch_id or ""),
        outcome=outcome,
        reason=str(reason or ""),
        before=before,
        after=after,
        ip_address=(client_ip(request) or "") if request is not None else "",
        method=getattr(request, "method", "") if request is not None else "",
        path=(request.path[:255] if request is not None else ""),
        rule_version=rule_version or "",
    )
