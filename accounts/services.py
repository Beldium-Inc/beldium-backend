import secrets
import logging
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from accounts.models import EmailVerificationCode, User
from common.exceptions import AppError

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
logger = logging.getLogger(__name__)


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def enqueue_verification_email(user_id, code):
    from accounts.tasks import send_email_verification

    try:
        send_email_verification.delay(str(user_id), code)
    except Exception:
        logger.exception(
            "Unable to enqueue email verification message",
            extra={"user_id": str(user_id)},
        )


def issue_email_verification(user):
    """Invalidate previous codes, persist a hash, and enqueue the plaintext code after commit."""
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
        user = User.objects.select_for_update().filter(email__iexact=email).first()
        if user and not user.email_verified_at:
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
        raise AppError(
            "Invalid or expired verification code.",
            code="invalid_verification_code",
        )
    return verified_user
