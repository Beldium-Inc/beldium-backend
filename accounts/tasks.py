import logging
import json
from urllib.request import Request, urlopen

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from accounts.models import User

logger = logging.getLogger(__name__)


def _send_template(*, recipient, subject, template, context):
    html = render_to_string(template, context)
    email = EmailMultiAlternatives(
        subject=subject,
        body=strip_tags(html),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    email.attach_alternative(html, "text/html")
    email.send(fail_silently=False)


@shared_task(
    bind=True,
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    name="accounts.send_email_verification",
)
def send_email_verification(self, user_id, code):
    user = User.objects.filter(id=user_id).only("email", "first_name").first()
    if not user or user.email_verified_at:
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


@shared_task(name="accounts.send_password_reset_email")
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


@shared_task(name="accounts.send_email_change_email")
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


@shared_task(name="accounts.send_welcome_email")
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


@shared_task(name="accounts.send_organisation_invitation_email")
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


@shared_task(name="accounts.send_phone_verification")
def send_phone_verification(user_id, phone_number, code):
    """Send through a configurable SMS webhook; log safely in local development."""
    webhook = getattr(settings, "SMS_WEBHOOK_URL", "")
    if not webhook:
        # No gateway configured. In development, print the code the way the
        # console email backend prints the signup code — otherwise the phone
        # step cannot be completed locally at all. Never outside DEBUG: an
        # OTP in a production log is an OTP in whatever ships those logs.
        if settings.DEBUG:
            logger.warning(
                "SMS gateway not configured; phone verification code for %s is %s", phone_number, code
            )
        else:
            logger.info(
                "Phone verification generated",
                extra={"user_id": user_id, "phone_number": phone_number[-4:]},
            )
        return
    payload = json.dumps({"to": phone_number, "message": f"Your Beldium verification code is {code}. It expires in 10 minutes."}).encode()
    request = Request(webhook, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {getattr(settings, 'SMS_WEBHOOK_TOKEN', '')}"}, method="POST")
    with urlopen(request, timeout=10) as response:
        if response.status >= 400:
            raise ConnectionError(f"SMS gateway returned {response.status}")
