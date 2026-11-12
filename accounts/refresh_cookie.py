"""The refresh token in an HttpOnly cookie (task G9).

Script on the page can never read the cookie, so a cross site scripting bug
cannot steal the long lived token. The cookie is limited to the auth routes,
marked Secure outside local development, and SameSite=Lax: the portals and
the API share the beldium.com site, so the portals' own calls carry it while
other sites' calls do not.
"""
from django.conf import settings

from rest_framework_simplejwt.settings import api_settings

AUTH_PATH = "/api/v1/auth/"
# Partner field roles whose sessions may refresh after the access token lapses.
FIELD_ROLES = {"SMP", "INS", "LOG"}


def cookie_name():
    return getattr(settings, "REFRESH_COOKIE_NAME", "beldium_refresh")


def cookie_refresh_token(request):
    return request.COOKIES.get(cookie_name(), "")


def set_refresh_cookie(response, refresh):
    response.set_cookie(
        cookie_name(), refresh,
        max_age=int(api_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        path=AUTH_PATH, secure=not settings.DEBUG, httponly=True, samesite="Lax",
    )
    return response


def clear_refresh_cookie(response):
    response.delete_cookie(cookie_name(), path=AUTH_PATH, samesite="Lax")
    return response


def attach_refresh_cookie(response):
    """Put the refresh token from a token answer into the cookie, and drop it
    from the body once REFRESH_TOKEN_IN_BODY is off."""
    data = getattr(response, "data", None)
    if isinstance(data, dict) and data.get("refresh"):
        set_refresh_cookie(response, data["refresh"])
        if not getattr(settings, "REFRESH_TOKEN_IN_BODY", True):
            data.pop("refresh")
    return response


def refresh_without_access(user):
    from quality.permissions import role_codes

    return bool(role_codes(user) & FIELD_ROLES)
