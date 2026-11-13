"""Two-step sign-in with an authenticator app.

Flow:
1. A signed-in user starts setup: we make a secret and show it as a QR code.
2. They type the six-digit code their app shows: setup is confirmed and they
   get ten single-use backup codes, shown once.
3. From then on, a correct password at /auth/token/ answers with a short-lived
   challenge instead of tokens, and /auth/mfa/verify/ swaps the challenge plus
   a current code (or a backup code) for the token pair.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from accounts import totp
from accounts.models import AuthenticatorDevice, MfaBackupCode, User
from common.exceptions import AppError, ConflictError

CHALLENGE_SALT = "accounts.mfa.challenge"
CHALLENGE_MAX_AGE_SECONDS = 5 * 60
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
BACKUP_CODE_COUNT = 10
# No 0/O, 1/I/L: backup codes get written down and typed back in.
_BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class InvalidMfaCode(AppError):
    def __init__(self):
        super().__init__("That code is not right. Check your authenticator app and try again.",
                         code="mfa_code_invalid", status_code=400)


class MfaLocked(AppError):
    def __init__(self):
        super().__init__("Too many wrong codes. Wait 15 minutes and try again.",
                         code="mfa_locked", status_code=429)


def confirmed_device(user) -> AuthenticatorDevice | None:
    device = AuthenticatorDevice.objects.filter(user=user).first()
    return device if device and device.is_confirmed else None


def is_enabled(user) -> bool:
    return confirmed_device(user) is not None


def status_for(user) -> dict:
    device = confirmed_device(user)
    return {
        "enabled": device is not None,
        "confirmed_at": device.confirmed_at if device else None,
        "backup_codes_remaining": MfaBackupCode.objects.filter(user=user, used_at__isnull=True).count() if device else 0,
    }


# --- setup -------------------------------------------------------------------

def start_setup(user) -> dict:
    """Make (or remake) an unconfirmed secret. Refused once two-step sign-in is on."""
    if is_enabled(user):
        raise ConflictError("Two-step sign-in is already on. Turn it off first to pair a new app.",
                            code="mfa_already_enabled")
    secret = totp.generate_secret()
    AuthenticatorDevice.objects.update_or_create(
        user=user,
        defaults={"secret": secret, "confirmed_at": None, "last_used_step": None,
                  "failed_attempts": 0, "locked_until": None},
    )
    return {"secret": secret, "otpauth_url": totp.provisioning_uri(secret, user.email)}


def confirm_setup(user, code) -> list[str]:
    # Not atomic: a wrong code's failure count has to be saved even though
    # the call then raises.
    device = AuthenticatorDevice.objects.filter(user=user).first()
    if device is None:
        raise AppError("Start setup first.", code="mfa_setup_not_started", status_code=400)
    if device.is_confirmed:
        raise ConflictError("Two-step sign-in is already on.", code="mfa_already_enabled")
    _check_totp(device, code)
    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed_at", "updated_at"])
    return _replace_backup_codes(user)


def disable(user, code) -> None:
    """Needs a current code (or backup code) as well as the password the view checks."""
    device = AuthenticatorDevice.objects.filter(user=user).first()
    if device is None or not device.is_confirmed:
        raise ConflictError("Two-step sign-in is not on.", code="mfa_not_enabled")
    verify(device, code)
    with transaction.atomic():
        device.delete()
        MfaBackupCode.objects.filter(user=user).delete()


def regenerate_backup_codes(user, code) -> list[str]:
    device = AuthenticatorDevice.objects.filter(user=user).first()
    if device is None or not device.is_confirmed:
        raise ConflictError("Two-step sign-in is not on.", code="mfa_not_enabled")
    _check_totp(device, code)
    return _replace_backup_codes(user)


# --- checking codes ----------------------------------------------------------

def _hash_backup(code: str) -> str:
    clean = "".join(ch for ch in code.lower() if ch.isalnum())
    return hmac.new(settings.SECRET_KEY.encode(), clean.encode(), hashlib.sha256).hexdigest()


@transaction.atomic
def _replace_backup_codes(user) -> list[str]:
    MfaBackupCode.objects.filter(user=user).delete()
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    MfaBackupCode.objects.bulk_create([MfaBackupCode(user=user, code_hash=_hash_backup(c)) for c in codes])
    return codes


def _guard_lock(device):
    if device.locked_until and device.locked_until > timezone.now():
        raise MfaLocked()


def _record_failure(device):
    device.failed_attempts += 1
    fields = ["failed_attempts", "updated_at"]
    if device.failed_attempts >= MAX_FAILED_ATTEMPTS:
        device.locked_until = timezone.now() + LOCKOUT
        device.failed_attempts = 0
        fields.append("locked_until")
    device.save(update_fields=fields)


def _record_success(device, step=None):
    device.failed_attempts = 0
    device.locked_until = None
    device.last_used_at = timezone.now()
    fields = ["failed_attempts", "locked_until", "last_used_at", "updated_at"]
    if step is not None:
        device.last_used_step = step
        fields.append("last_used_step")
    device.save(update_fields=fields)


def _check_totp(device, code):
    """Authenticator codes only (setup and backup-code regeneration)."""
    _guard_lock(device)
    step = totp.matching_step(device.secret, code, after_step=device.last_used_step)
    if step is None:
        _record_failure(device)
        raise InvalidMfaCode()
    _record_success(device, step)


def verify(device, code) -> str:
    """Accept a current authenticator code or an unused backup code. Returns which was used.

    Must not run inside a transaction that the raised error rolls back, or the
    failure count is lost and the lockout never triggers.
    """
    _guard_lock(device)
    code = str(code or "").strip()
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) == totp.DIGITS and len(digits) == len(code.replace(" ", "")):
        step = totp.matching_step(device.secret, digits, after_step=device.last_used_step)
        if step is not None:
            _record_success(device, step)
            return "totp"
    else:
        backup = MfaBackupCode.objects.filter(
            user=device.user, used_at__isnull=True, code_hash=_hash_backup(code)
        ).first()
        if backup is not None:
            backup.used_at = timezone.now()
            backup.save(update_fields=["used_at", "updated_at"])
            _record_success(device)
            return "backup_code"
    _record_failure(device)
    raise InvalidMfaCode()


# --- sign-in challenge -------------------------------------------------------

def issue_challenge(user, portal: str) -> str:
    """Proof the password was right, good for five minutes, swapped at /mfa/verify/.

    Carries the portal the password was checked for, so it cannot be redeemed
    from another Beldium app.
    """
    return signing.dumps({"u": str(user.pk), "p": portal}, salt=CHALLENGE_SALT)


def user_for_challenge(token: str, portal: str) -> User:
    try:
        payload = signing.loads(token, salt=CHALLENGE_SALT, max_age=CHALLENGE_MAX_AGE_SECONDS)
    except signing.SignatureExpired as exc:
        raise AppError("Your sign-in took too long. Enter your password again.",
                       code="mfa_challenge_expired", status_code=401) from exc
    except signing.BadSignature as exc:
        raise AppError("Sign in again.", code="mfa_challenge_invalid", status_code=401) from exc
    user = User.objects.filter(pk=payload.get("u"), is_active=True).first()
    if user is None or payload.get("p") != portal:
        raise AppError("Sign in again.", code="mfa_challenge_invalid", status_code=401)
    return user
