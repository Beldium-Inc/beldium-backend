"""Processing audit trail.

Written into the shared ``AccountAuditEvent`` table alongside the account and
compliance trails, so one query answers "what has this user done" across the
platform. The rendered ``target``/``detail`` strings live in the metadata
because the audit desk reads them as a sentence, not as a row of foreign keys.
"""
from accounts.audit import record_account_event

PREFIX = "processing."


def record(request, action, *, target="", detail="", actor=None, **metadata):
    return record_account_event(
        request,
        f"{PREFIX}{action}",
        actor=actor,
        target=target,
        detail=detail,
        **metadata,
    )
