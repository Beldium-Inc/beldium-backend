"""Second factor at sign in: an authenticator app (TOTP, RFC 6238).

Who must use it: every user who holds a Q&C role other than Supplier, Buyer
and Export agent (pack: internal and partner users), once
SECOND_FACTOR_ENFORCED is on. Until it is on, enrolment works but sign in
issues tokens as before, so the frontend step (task E13) can ship first.

The shared secret is encrypted with a key from SECOND_FACTOR_ENCRYPTION_KEY,
or derived from SECRET_KEY when that is not set. Changing that key makes
every enrolled app unusable, so set SECOND_FACTOR_ENCRYPTION_KEY before
SECRET_KEY is ever rotated.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.exceptions import AppError

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # one step either side, for phone clocks that drift a little
CHALLENGE_SECONDS = 300
MAX_FAILED_ATTEMPTS = 5
RECOVERY_CODE_COUNT = 10
ISSUER = "Beldium"
NO_SECOND_FACTOR_ROLES = {"SUP", "BYR", "EXP"}


# --- TOTP ---------------------------------------------------------------------


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_for_step(secret, step):
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now=None):
    return int((now if now is not None else time.time()) // STEP_SECONDS)


def code_at(secret, now=None):
    return _code_for_step(secret, current_step(now))


def matching_step(secret, code, *, after_step=0, now=None):
    """The time step the code belongs to, or None. Steps at or before
    after_step are refused, so a code is never accepted twice."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if candidate > after_step and hmac.compare_digest(_code_for_step(secret, candidate), code):
            return candidate
    return None


def otpauth_uri(secret, account_name):
    label = quote(f"{ISSUER}:{account_name}")
    query = urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP_SECONDS})
    return f"otpauth://totp/{label}?{query}"


# --- secret storage -----------------------------------------------------------


def _fernet():
    configured = getattr(settings, "SECOND_FACTOR_ENCRYPTION_KEY", "")
    if configured:
        return Fernet(configured.encode())
    derived = hashlib.sha256(f"second-factor:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(secret):
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise AppError(
            "The second factor cannot be checked. Ask Beldium support to reset it.",
            code="second_factor_unreadable", status_code=500,
        ) from exc


# --- who must use it ----------------------------------------------------------


def is_enrolled(user):
    from accounts.models import SecondFactorDevice

    return SecondFactorDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def is_required(user):
    if not getattr(settings, "SECOND_FACTOR_ENFORCED", False):
        return False
    from quality.permissions import role_codes

    return bool(role_codes(user) - NO_SECOND_FACTOR_ROLES)


# --- challenges ---------------------------------------------------------------


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def start_challenge(user):
    from accounts.models import SecondFactorChallenge

    token = secrets.token_urlsafe(32)
    SecondFactorChallenge.objects.create(
        user=user, token_hash=_hash(token),
        expires_at=timezone.now() + timezone.timedelta(seconds=CHALLENGE_SECONDS),
    )
    return {
        "second_factor_required": True,
        "challenge_token": token,
        "methods": ["totp"],
        "enrolment_required": not is_enrolled(user),
        "expires_in": CHALLENGE_SECONDS,
    }


def open_challenge(token):
    """The challenge row, locked for this transaction, or an error."""
    from accounts.models import SecondFactorChallenge

    challenge = (
        SecondFactorChallenge.objects.select_for_update().select_related("user")
        .filter(token_hash=_hash(token or "")).first()
    )
    if challenge is None or challenge.used_at or challenge.expires_at <= timezone.now() or not challenge.user.is_active:
        raise AppError("Sign in again. The code step took too long.", code="challenge_expired", status_code=401)
    if challenge.failed_attempts >= MAX_FAILED_ATTEMPTS:
        raise AppError("Too many wrong codes. Try again in 15 minutes.", code="second_factor_locked", status_code=429)
    return challenge


def close_challenge(challenge):
    challenge.used_at = timezone.now()
    challenge.save(update_fields=["used_at", "updated_at"])


def fail_challenge(challenge):
    challenge.failed_attempts += 1
    challenge.save(update_fields=["failed_attempts", "updated_at"])


# --- checking a code ----------------------------------------------------------


def check_code(user, code):
    """True when code is a current authenticator code or an unused recovery
    code. Each is accepted once."""
    from accounts.models import SecondFactorDevice, SecondFactorRecoveryCode

    device = SecondFactorDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        raise AppError("Set up your second factor first.", code="second_factor_not_enrolled", status_code=403)
    step = matching_step(decrypt_secret(device.secret_encrypted), code, after_step=device.last_used_step)
    if step is not None:
        device.last_used_step = step
        device.save(update_fields=["last_used_step", "updated_at"])
        return True
    cleaned = (code or "").strip().replace("-", "").upper()
    if cleaned:
        recovery = SecondFactorRecoveryCode.objects.select_for_update().filter(
            user=user, code_hash=_hash(cleaned), used_at__isnull=True,
        ).first()
        if recovery:
            recovery.used_at = timezone.now()
            recovery.save(update_fields=["used_at", "updated_at"])
            return True
    return False


# --- enrolment ----------------------------------------------------------------


@transaction.atomic
def begin_enrolment(user):
    from accounts.models import SecondFactorDevice

    device = SecondFactorDevice.objects.select_for_update().filter(user=user).first()
    if device and device.confirmed_at:
        raise AppError(
            "A second factor is already set up for this account.",
            code="second_factor_already_enrolled", status_code=409,
        )
    secret = new_secret()
    if device:
        device.secret_encrypted = encrypt_secret(secret)
        device.save(update_fields=["secret_encrypted", "updated_at"])
    else:
        SecondFactorDevice.objects.create(user=user, secret_encrypted=encrypt_secret(secret))
    return {"method": "totp", "secret": secret, "otpauth_uri": otpauth_uri(secret, user.email)}


def confirm_enrolment(user, code):
    """Confirm the app with its first code. Returns the recovery codes, shown once."""
    from accounts.models import SecondFactorDevice, SecondFactorRecoveryCode

    device = SecondFactorDevice.objects.select_for_update().filter(user=user).first()
    if device is None:
        raise AppError("Start the set up first.", code="second_factor_not_started", status_code=409)
    if device.confirmed_at:
        raise AppError(
            "A second factor is already set up for this account.",
            code="second_factor_already_enrolled", status_code=409,
        )
    step = matching_step(decrypt_secret(device.secret_encrypted), code)
    if step is None:
        return None
    device.confirmed_at = timezone.now()
    device.last_used_step = step
    device.save(update_fields=["confirmed_at", "last_used_step", "updated_at"])
    codes = [secrets.token_hex(5).upper() for _ in range(RECOVERY_CODE_COUNT)]
    SecondFactorRecoveryCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    SecondFactorRecoveryCode.objects.bulk_create(
        [SecondFactorRecoveryCode(user=user, code_hash=_hash(code_value)) for code_value in codes]
    )
    return [f"{value[:5]}-{value[5:]}" for value in codes]
