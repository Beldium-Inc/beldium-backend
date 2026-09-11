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


@register()
def throttle_cache_is_shared(app_configs, **kwargs):
    """Every rate limit in this project counts in the default cache.

    LocMemCache is per-process, so under any real server each worker keeps its
    own private tally and the effective limit is the configured one multiplied
    by the worker count — with the counters resetting whenever a worker
    recycles. The limits protecting sign-in and the OTP endpoints are only as
    real as the cache behind them.
    """
    backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if "locmem" not in backend.lower():
        return []

    detail = (
        "The default cache is LocMemCache, which is per-process: rate limits on "
        "sign-in, registration and the verification endpoints will not hold across "
        "workers. Set CACHE_URL to a shared Redis instance."
    )
    if settings.ENVIRONMENT == "production":
        return [Error(detail, id="beldium.E002")]
    return [CheckWarning(detail, id="beldium.W002")]


@register()
def forwarded_header_trust_is_declared(app_configs, **kwargs):
    """NUM_PROXIES decides how much of X-Forwarded-For we believe.

    Unset, DRF trusts the leftmost entry — which the client writes — so every
    IP-keyed throttle can be bypassed by varying a header, and every audit
    record can be attributed to an address of the caller's choosing.
    """
    num_proxies = settings.REST_FRAMEWORK.get("NUM_PROXIES", "unset")
    if isinstance(num_proxies, int) and num_proxies >= 0:
        return []
    return [Error(
        "REST_FRAMEWORK['NUM_PROXIES'] must be set to the number of proxies in front "
        "of this service (0 when there are none). Without it X-Forwarded-For is trusted "
        "as sent, which makes IP-based throttling and the audit trail forgeable.",
        id="beldium.E003",
    )]
