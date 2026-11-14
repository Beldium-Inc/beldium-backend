"""Time-based one-time passwords (RFC 6238), as authenticator apps produce them.

Google Authenticator, Microsoft Authenticator, Authy, 1Password and the rest
all speak the same defaults: a base32 secret, HMAC-SHA1, 30 second steps and
six digits. Implemented with the standard library so the backend takes on no
new dependency for it.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
# Accept the step before and after the current one, so a phone clock that is
# up to 30 seconds out, or a code typed as it rolls over, still works.
DRIFT_STEPS = 1
ISSUER = "Beldium"


def generate_secret() -> str:
    """A fresh 160-bit secret, base32 encoded without padding."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at_step(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def matching_step(secret: str, code: str, *, after_step: int | None = None, now: float | None = None) -> int | None:
    """The time step ``code`` belongs to, or None if it matches none in the window.

    Steps at or before ``after_step`` are refused, so a code that was already
    used cannot be replayed within its 90 second window.
    """
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if after_step is not None and candidate <= after_step:
            continue
        if hmac.compare_digest(code_at_step(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account_name: str) -> str:
    """The otpauth:// link an authenticator app reads from the QR code."""
    label = quote(f"{ISSUER}:{account_name}")
    query = urlencode({"secret": secret, "issuer": ISSUER, "algorithm": "SHA1", "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"
