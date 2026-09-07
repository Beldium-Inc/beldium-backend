from accounts.audit import record_account_event


def record_event(request, event_type, *, actor=None, organisation=None, **metadata):
    if organisation:
        metadata["organisation_id"] = str(organisation.id)
    return record_account_event(request, event_type, actor=actor, **metadata)
