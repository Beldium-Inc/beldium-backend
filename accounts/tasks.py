import logging
import signal
import threading

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from accounts.models import User

logger = logging.getLogger(__name__)

EMAIL_SEND_HARD_TIMEOUT_SECONDS = 15


def _alarm_handler(signum, frame):
    raise TimeoutError("email send exceeded hard timeout")


def _send_template(*, recipient, subject, template, context):
    html = render_to_string(template, context)
    email = EmailMultiAlternatives(
        subject=subject,
        body=strip_tags(html),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    email.attach_alternative(html, "text/html")
    logger.info("Sending email via %s to %s: %s", settings.EMAIL_BACKEND, recipient, subject)
    # settings.EMAIL_TIMEOUT only bounds the connect() call, not DNS
    # resolution — a stalled lookup on the host's resolver can hang well
    # past it (observed in production: 10s EMAIL_TIMEOUT, 100+s actual
    # hang). SIGALRM interrupts the send regardless of what it's stuck on,
    # but only works on the main thread. Sends now happen on a background
    # thread (accounts.services._run_in_background) so the request doesn't
    # wait on them, so this only applies when something calls this directly
    # from the main thread (e.g. a test using .run()) — off the main thread
    # it's skipped and EMAIL_TIMEOUT is the only bound, which is an
    # acceptable trade since a stuck send there no longer blocks a request.
    on_main_thread = threading.current_thread() is threading.main_thread()
    if on_main_thread:
        previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(EMAIL_SEND_HARD_TIMEOUT_SECONDS)
    try:
        email.send(fail_silently=False)
    finally:
        if on_main_thread:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
    logger.info("Sent email to %s: %s", recipient, subject)


@shared_task(
    bind=True,
    name="accounts.send_email_verification",
)
def send_email_verification(self, user_id, code):
    user = User.objects.filter(id=user_id).only("email", "first_name").first()
    if not user or user.email_verified_at:
        logger.info(
            "Skipping verification email for user_id=%s: found=%s already_verified=%s",
            user_id, bool(user), bool(user and user.email_verified_at),
        )
        return

    context = {
        "first_name": user.first_name,
        "verification_code": code,
        "expires_in_minutes": 10,
        "frontend_url": settings.FRONTEND_URL,
    }
    _send_template(
        recipient=user.email,
        subject="Verify your Beldium account",
        template="accounts/email_verification.html",
        context=context,
    )
    logger.info("Email verification message sent", extra={"user_id": str(user.id)})


@shared_task(
    name="accounts.send_password_reset_email",
)
def send_password_reset_email(user_id, code):
    user = User.objects.filter(id=user_id).only("email", "first_name").first()
    if not user:
        return
    _send_template(
        recipient=user.email,
        subject="Reset your Beldium password",
        template="accounts/security_code.html",
        context={"first_name": user.first_name, "verification_code": code, "purpose": "reset your password", "expires_in_minutes": 10},
    )


@shared_task(
    name="accounts.send_email_change_email",
)
def send_email_change_email(user_id, new_email, code):
    user = User.objects.filter(id=user_id).only("first_name").first()
    if not user:
        return
    _send_template(
        recipient=new_email,
        subject="Confirm your new Beldium email",
        template="accounts/security_code.html",
        context={"first_name": user.first_name, "verification_code": code, "purpose": "confirm your new email address", "expires_in_minutes": 10},
    )


@shared_task(
    name="accounts.send_welcome_email",
)
def send_welcome_email(user_id):
    user = User.objects.filter(id=user_id).only("email", "first_name").first()
    if not user:
        return
    _send_template(
        recipient=user.email,
        subject="Welcome to Beldium Mining Compliance",
        template="accounts/welcome.html",
        context={"first_name": user.first_name, "frontend_url": settings.FRONTEND_URL},
    )


@shared_task(
    name="accounts.send_organisation_invitation_email",
)
def send_organisation_invitation_email(email_address, organisation_name, inviter_name, role, token):
    _send_template(
        recipient=email_address,
        subject=f"Join {organisation_name} on Beldium",
        template="accounts/organisation_invitation.html",
        context={
            "organisation_name": organisation_name,
            "inviter_name": inviter_name,
            "role": role,
            "accept_url": f"{settings.FRONTEND_URL.rstrip('/')}/onboarding?invitation={token}",
        },
    )


def _frontend_link(path):
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}"


@shared_task(
    name="accounts.send_join_request_received_email",
)
def send_join_request_received_email(email_address, organisation_name, requester_name, requester_email, role):
    """Tells an owner or administrator that someone asked to join."""
    _send_template(
        recipient=email_address,
        subject=f"{requester_name} asked to join {organisation_name} on Beldium",
        template="accounts/join_request_received.html",
        context={
            "organisation_name": organisation_name,
            "requester_name": requester_name,
            "requester_email": requester_email,
            "role": role,
            "review_url": _frontend_link("/onboarding/admin"),
        },
    )


@shared_task(
    name="accounts.send_join_request_submitted_email",
)
def send_join_request_submitted_email(email_address, first_name, organisation_name, role):
    """Confirms to the requester that their request reached the organisation."""
    _send_template(
        recipient=email_address,
        subject=f"Your request to join {organisation_name} was sent",
        template="accounts/join_request_submitted.html",
        context={
            "first_name": first_name,
            "organisation_name": organisation_name,
            "role": role,
            "status_url": _frontend_link("/onboarding/join"),
        },
    )


@shared_task(
    name="accounts.send_join_request_decided_email",
)
def send_join_request_decided_email(email_address, first_name, organisation_name, approved, role, notes):
    """Tells the requester whether an administrator accepted or declined them."""
    _send_template(
        recipient=email_address,
        subject=(
            f"You now have access to {organisation_name} on Beldium"
            if approved
            else f"Your request to join {organisation_name} was declined"
        ),
        template="accounts/join_request_decided.html",
        context={
            "first_name": first_name,
            "organisation_name": organisation_name,
            "approved": approved,
            "role": role,
            "notes": notes,
            "signin_url": _frontend_link("/signin"),
        },
    )


@shared_task(
    bind=True,
    name="accounts.send_phone_verification",
)
def send_phone_verification(self, user_id, phone_number, code):
    """Send the phone code through the shared SMS path (common/sms.py).

    Sending stays off until SMS_ENABLED is set, once the Termii sender ID is
    approved. While it is off, local development logs the code so it can be
    typed in, and every attempt is still written as a delivery row.
    """
    from common.sms import send_sms

    delivery = send_sms(
        phone_number,
        f"Your Beldium verification code is {code}. It expires in 10 minutes.",
        purpose="phone_verification",
    )
    if delivery.status == "skipped" and settings.DEBUG:
        logger.warning("SMS sending off; phone verification code for %s is %s", phone_number, code)
    elif delivery.status == "skipped":
        logger.info(
            "Phone verification generated (SMS sending off)",
            extra={"user_id": user_id, "phone_number": phone_number[-4:]},
        )
    return delivery.status
