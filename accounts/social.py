from django.conf import settings

from common.exceptions import AppError


def verify_google_token(token):
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        raise AppError("Google sign-in is not configured.", code="social_provider_not_configured", status_code=503)
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), settings.GOOGLE_OAUTH_CLIENT_ID)
    except Exception as exc:
        raise AppError("Invalid Google identity token.", code="invalid_social_token", status_code=401) from exc
    if not claims.get("email") or not claims.get("email_verified"):
        raise AppError("Google has not verified this email address.", code="social_email_not_verified", status_code=403)
    return {"subject": claims["sub"], "email": claims["email"], "name": claims.get("name", "")}


def verify_microsoft_token(token):
    if not settings.MICROSOFT_OAUTH_CLIENT_ID:
        raise AppError("Microsoft sign-in is not configured.", code="social_provider_not_configured", status_code=503)
    try:
        import jwt
        unverified = jwt.decode(token, options={"verify_signature": False})
        tenant_id = unverified.get("tid")
        if not tenant_id:
            raise ValueError("Missing tenant")
        configured_tenant = settings.MICROSOFT_OAUTH_TENANT_ID
        if configured_tenant not in {"common", "organizations", "consumers"} and tenant_id != configured_tenant:
            raise ValueError("Unexpected tenant")
        issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        key = jwt.PyJWKClient("https://login.microsoftonline.com/common/discovery/v2.0/keys").get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=settings.MICROSOFT_OAUTH_CLIENT_ID, issuer=issuer)
    except Exception as exc:
        raise AppError("Invalid Microsoft identity token.", code="invalid_social_token", status_code=401) from exc
    # preferred_username is a display hint, not a proven address — on personal
    # accounts the user sets it. The caller marks whatever comes back here as a
    # verified email, so only the email claim will do, and xms_edov (set when
    # the tenant has proven it owns the domain) must not contradict it.
    email = claims.get("email")
    subject = claims.get("oid") or claims.get("sub")
    if not email or not subject:
        raise AppError("Microsoft did not provide the required identity claims.", code="social_identity_incomplete", status_code=400)
    if claims.get("xms_edov") is False:
        raise AppError("Microsoft has not verified this email address.", code="social_email_not_verified", status_code=403)
    return {"subject": subject, "email": email, "name": claims.get("name", "")}


def verify_social_token(provider, token):
    return verify_google_token(token) if provider == "google" else verify_microsoft_token(token)
