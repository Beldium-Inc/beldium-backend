"""Mining audit trail.

Written into the shared ``AccountAuditEvent`` table alongside the account,
processing and compliance trails, so one query answers "what has this user
done" across the platform. Mirrors ``processing.audit`` exactly.
"""
from accounts.audit import record_account_event

PREFIX = "mining."


def record(request, action, *, target="", detail="", actor=None, **metadata):
    return record_account_event(
        request,
        f"{PREFIX}{action}",
        actor=actor,
        target=target,
        detail=detail,
        **metadata,
    )
