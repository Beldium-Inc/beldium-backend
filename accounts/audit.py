from accounts.models import AccountAuditEvent


def request_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")


def record_account_event(request, event_type, *, actor=None, **metadata):
    return AccountAuditEvent.objects.create(
        actor=actor if actor is not None else (request.user if request.user.is_authenticated else None),
        event_type=event_type,
        ip_address=request_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
        metadata=metadata,
    )
