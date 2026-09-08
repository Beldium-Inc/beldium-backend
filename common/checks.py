from django.conf import settings
from django.core.checks import Error, Warning as CheckWarning, register


# Placeholders that ship in this repository. Either of them reaching a running
# instance means nobody set a real key for that environment.
PLACEHOLDER_KEYS = frozenset({
    "change-me",
    "unsafe-development-key-change-this-before-any-real-deployment-2026",
})

MINIMUM_LENGTH = 50


@register()
def secret_key_is_strong(app_configs, **kwargs):
    """SECRET_KEY signs every access and refresh token this API issues.

    A short or placeholder key is a token-forgery risk rather than a cosmetic
    warning: SimpleJWT declares no SIGNING_KEY, so it falls back to SECRET_KEY
    and signs HS256 with it. Django's own security.W009 covers the same ground
    but only runs under `check --deploy`, which nothing in this project does.
    """
    key = settings.SECRET_KEY or ""

    if key in PLACEHOLDER_KEYS:
        reason = "it is a placeholder shipped in this repository"
    elif len(key) < MINIMUM_LENGTH:
        reason = f"it is {len(key)} characters, and HS256 signing wants at least {MINIMUM_LENGTH}"
    else:
        return []

    detail = (
        f"SECRET_KEY is unsafe: {reason}. It signs every access and refresh token, "
        "so anyone who recovers it can mint valid credentials for any account. "
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
    )

    if settings.ENVIRONMENT == "production":
        return [Error(detail, id="beldium.E001")]
    return [CheckWarning(detail, id="beldium.W001")]
