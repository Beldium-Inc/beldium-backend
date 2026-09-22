from django.conf import settings

from accounts.models import User


def portal_for_origin(origin: str) -> str:
    """Maps a request's Origin header to a User.Portal value.

    Returns "" (falsy, matching User.portal's blank default) when the origin
    is missing or not one of the configured frontends — callers decide
    whether that should block the request or fall back to some default.
    """
    origin = (origin or "").rstrip("/")
    if origin in settings.COMPLIANCE_PORTAL_ORIGINS:
        return User.Portal.COMPLIANCE
    if origin in settings.MINER_PORTAL_ORIGINS:
        return User.Portal.MINER
    return ""


def portal_for_request(request) -> str:
    return portal_for_origin(request.META.get("HTTP_ORIGIN", "")) if request else ""
