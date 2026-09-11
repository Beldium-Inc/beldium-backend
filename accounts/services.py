import secrets
import logging
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.models import AccountRecoveryCode, EmailVerificationCode, PhoneVerificationCode, User
from common.exceptions import AppError, ConflictError

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# max_attempts alone caps guesses per *code*, which caps nothing: issuing a
# fresh code resets the counter, so a caller who can reach the issue endpoint
# can buy another OTP_MAX_ATTEMPTS guesses as often as they like and walk the
# whole six-digit space. These two budgets are what actually bound an attack,
# and they are counted per user in the database rather than per caller in the
# cache so that neither rotating the code nor rotating the source address
# widens them.
OTP_BUDGET_WINDOW_MINUTES = 60
OTP_MAX_ISSUES_PER_WINDOW = 5
OTP_MAX_FAILURES_PER_WINDOW = 10

logger = logging.getLogger(__name__)


class VerificationBudgetExceeded(AppError):
    """Raised where the caller is already authenticated and can safely be told to wait."""

    def __init__(self):
        super().__init__(
            "Too many verification attempts. Please wait an hour and try again.",
            code="verification_budget_exceeded",
            status_code=429,
        )


def _in_budget_window(queryset):
    since = timezone.now() - timedelta(minutes=OTP_BUDGET_WINDOW_MINUTES)
    return queryset.filter(created_at__gte=since)


def issue_budget_exhausted(queryset):
    """True when this user has already been sent the most codes we allow per window."""
    return _in_budget_window(queryset).count() >= OTP_MAX_ISSUES_PER_WINDOW


def attempt_budget_exhausted(queryset):
    """True when failures across *every* recent code have used up the window's guesses."""
    total = _in_budget_window(queryset).aggregate(total=Sum("failed_attempts"))["total"] or 0
    return total >= OTP_MAX_FAILURES_PER_WINDOW


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def blacklist_user_refresh_tokens(user):
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def enqueue_verification_email(user_id, code):
    from accounts.tasks import send_email_verification

    try:
        send_email_verification.delay(str(user_id), code)
    except Exception:
        logger.exception(
            "Unable to enqueue email verification message",
            extra={"user_id": str(user_id)},
        )


def enqueue_account_email(task_name, *args):
    from accounts import tasks

    try:
        getattr(tasks, task_name).delay(*args)
    except Exception:
        logger.exception("Unable to enqueue account email", extra={"task_name": task_name})


def issue_email_verification(user):
    """Invalidate previous codes, persist a hash, and enqueue the plaintext code after commit.

    Silently does nothing once the issue budget is spent: this is reachable
    unauthenticated via the resend endpoint, whose whole point is to answer
    identically whatever the state of the address.
    """
    if issue_budget_exhausted(EmailVerificationCode.objects.filter(user=user)):
        logger.info("Email verification issue budget exhausted", extra={"user_id": str(user.id)})
        return
    code = generate_verification_code()
    now = timezone.now()
    with transaction.atomic():
        EmailVerificationCode.objects.select_for_update().filter(
            user=user, consumed_at__isnull=True
        ).update(consumed_at=now)
        EmailVerificationCode.objects.create(
            user=user,
            code_hash=make_password(code),
            expires_at=now + timedelta(minutes=OTP_TTL_MINUTES),
            max_attempts=OTP_MAX_ATTEMPTS,
        )

        transaction.on_commit(lambda: enqueue_verification_email(user.id, code))


def verify_email_code(email, submitted_code):
    verified_user = None
    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email, is_active=True).first()
        if user and not user.email_verified_at and not attempt_budget_exhausted(
            EmailVerificationCode.objects.filter(user=user)
        ):
            verification = EmailVerificationCode.objects.select_for_update().filter(
                user=user, consumed_at__isnull=True
            ).order_by("-created_at").first()
            now = timezone.now()
            if verification and verification.expires_at > now and verification.failed_attempts < verification.max_attempts:
                if check_password(submitted_code, verification.code_hash):
                    verification.consumed_at = now
                    verification.save(update_fields=["consumed_at", "updated_at"])
                    user.email_verified_at = now
                    user.save(update_fields=["email_verified_at", "updated_at"])
                    verified_user = user
                else:
                    verification.failed_attempts += 1
                    if verification.failed_attempts >= verification.max_attempts:
                        verification.consumed_at = now
                    verification.save(update_fields=["failed_attempts", "consumed_at", "updated_at"])
            elif verification and verification.consumed_at is None:
                verification.consumed_at = now
                verification.save(update_fields=["consumed_at", "updated_at"])

    if not verified_user:
        # Deliberately the same answer whether the address is unknown, already
        # verified, or the code is simply wrong: an unauthenticated caller must
        # not be able to enumerate accounts here. The pointer to signing in is
        # what keeps an already-verified user from being stuck on this screen.
        # The client offers the "already verified, sign in instead" way out, so
        # this stays a plain statement rather than repeating it.
        raise AppError(
            "That code is invalid or has expired.",
            code="invalid_verification_code",
        )
    return verified_user


def issue_phone_verification(user, phone_number):
    if issue_budget_exhausted(PhoneVerificationCode.objects.filter(user=user)):
        raise VerificationBudgetExceeded()
    code = generate_verification_code()
    now = timezone.now()
    with transaction.atomic():
        PhoneVerificationCode.objects.select_for_update().filter(user=user, consumed_at__isnull=True).update(consumed_at=now)
        PhoneVerificationCode.objects.create(
            user=user, phone_number=phone_number, code_hash=make_password(code),
            expires_at=now + timedelta(minutes=OTP_TTL_MINUTES), max_attempts=OTP_MAX_ATTEMPTS,
        )
        transaction.on_commit(lambda: enqueue_account_email("send_phone_verification", str(user.id), phone_number, code))


def verify_phone_code(user, phone_number, submitted_code):
    if attempt_budget_exhausted(PhoneVerificationCode.objects.filter(user=user)):
        raise VerificationBudgetExceeded()
    verified = False
    with transaction.atomic():
        verification = PhoneVerificationCode.objects.select_for_update().filter(
            user=user, phone_number=phone_number, consumed_at__isnull=True,
        ).order_by("-created_at").first()
        now = timezone.now()
        if verification and verification.expires_at > now and verification.failed_attempts < verification.max_attempts:
            if check_password(submitted_code, verification.code_hash):
                verification.consumed_at = now
                verification.save(update_fields=["consumed_at", "updated_at"])
                user.phone_number = phone_number
                user.phone_verified_at = now
                user.save(update_fields=["phone_number", "phone_verified_at", "updated_at"])
                verified = True
            else:
                verification.failed_attempts += 1
                if verification.failed_attempts >= verification.max_attempts:
                    verification.consumed_at = now
                verification.save(update_fields=["failed_attempts", "consumed_at", "updated_at"])
        elif verification:
            verification.consumed_at = now
            verification.save(update_fields=["consumed_at", "updated_at"])
    if not verified:
        raise AppError("Invalid or expired verification code.", code="invalid_verification_code")
    return user


def _recovery_codes(user, purpose):
    return AccountRecoveryCode.objects.filter(user=user, purpose=purpose)


def _issue_recovery_code(user, purpose, *, target_email=""):
    code = generate_verification_code()
    now = timezone.now()
    with transaction.atomic():
        AccountRecoveryCode.objects.select_for_update().filter(
            user=user, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=now)
        AccountRecoveryCode.objects.create(
            user=user,
            purpose=purpose,
            target_email=target_email,
            code_hash=make_password(code),
            expires_at=now + timedelta(minutes=OTP_TTL_MINUTES),
            max_attempts=OTP_MAX_ATTEMPTS,
        )
    return code


def issue_password_reset(email):
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if not user:
        return
    if issue_budget_exhausted(_recovery_codes(user, AccountRecoveryCode.Purpose.PASSWORD_RESET)):
        # Same silence as an unknown address: this endpoint's contract is that
        # its response says nothing about the account behind the email.
        logger.info("Password reset issue budget exhausted", extra={"user_id": str(user.id)})
        return
    code = _issue_recovery_code(user, AccountRecoveryCode.Purpose.PASSWORD_RESET)
    transaction.on_commit(lambda: enqueue_account_email("send_password_reset_email", str(user.id), code))


def issue_email_change(user, new_email):
    normalized = User.objects.normalize_email(new_email).lower()
    if normalized == user.email.lower() or User.objects.filter(email__iexact=normalized).exclude(pk=user.pk).exists():
        raise ConflictError("That email address cannot be used.", code="email_unavailable")
    if issue_budget_exhausted(_recovery_codes(user, AccountRecoveryCode.Purpose.EMAIL_CHANGE)):
        raise VerificationBudgetExceeded()
    code = _issue_recovery_code(user, AccountRecoveryCode.Purpose.EMAIL_CHANGE, target_email=normalized)
    transaction.on_commit(lambda: enqueue_account_email("send_email_change_email", str(user.id), normalized, code))


def _consume_recovery_code(user, purpose, submitted_code, *, target_email="", on_success=None, budget_error=None):
    if attempt_budget_exhausted(_recovery_codes(user, purpose)):
        # Callers that are already authenticated pass a budget_error and get
        # told to wait. Password reset does not: it falls through to the
        # ordinary invalid-code error so that an account under attack stays
        # indistinguishable from an address we have never seen.
        if budget_error:
            raise budget_error()
        raise AppError("Invalid or expired verification code.", code="invalid_verification_code")
    consumed = False
    with transaction.atomic():
        verification = AccountRecoveryCode.objects.select_for_update().filter(
            user=user,
            purpose=purpose,
            target_email=target_email,
            consumed_at__isnull=True,
        ).order_by("-created_at").first()
        now = timezone.now()
        if verification and verification.expires_at > now and verification.failed_attempts < verification.max_attempts:
            if check_password(submitted_code, verification.code_hash):
                if on_success:
                    on_success()
                verification.consumed_at = now
                verification.save(update_fields=["consumed_at", "updated_at"])
                consumed = True
            else:
                verification.failed_attempts += 1
                if verification.failed_attempts >= verification.max_attempts:
                    verification.consumed_at = now
                verification.save(update_fields=["failed_attempts", "consumed_at", "updated_at"])
        elif verification:
            verification.consumed_at = now
            verification.save(update_fields=["consumed_at", "updated_at"])
    if not consumed:
        raise AppError("Invalid or expired verification code.", code="invalid_verification_code")


def reset_password(email, code, new_password):
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if not user:
        raise AppError("Invalid or expired verification code.", code="invalid_verification_code")
    def update_password():
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

    _consume_recovery_code(
        user,
        AccountRecoveryCode.Purpose.PASSWORD_RESET,
        code,
        on_success=update_password,
    )
    blacklist_user_refresh_tokens(user)
    return user


def confirm_email_change(user, new_email, code):
    normalized = User.objects.normalize_email(new_email).lower()
    if User.objects.filter(email__iexact=normalized).exclude(pk=user.pk).exists():
        raise ConflictError("That email address cannot be used.", code="email_unavailable")
    def update_email():
        user.email = normalized
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email", "email_verified_at", "updated_at"])

    _consume_recovery_code(
        user,
        AccountRecoveryCode.Purpose.EMAIL_CHANGE,
        code,
        target_email=normalized,
        on_success=update_email,
        budget_error=VerificationBudgetExceeded,
    )
    blacklist_user_refresh_tokens(user)
    return user
