from accounts.models import AccountAuditEvent
from common.ip import client_ip


def request_ip(request):
    return client_ip(request)


def record_account_event(request, event_type, *, actor=None, **metadata):
    return AccountAuditEvent.objects.create(
        actor=actor if actor is not None else (request.user if request.user.is_authenticated else None),
        event_type=event_type,
        ip_address=request_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
        metadata=metadata,
    )
