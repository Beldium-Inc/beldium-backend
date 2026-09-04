import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from accounts.models import User

logger = logging.getLogger(__name__)


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

    html = render_to_string("accounts/email_verification.html", {
        "first_name": user.first_name,
        "verification_code": code,
        "expires_in_minutes": 10,
        "frontend_url": settings.FRONTEND_URL,
    })
    email = EmailMultiAlternatives(
        subject="Verify your Beldium account",
        body=strip_tags(html),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    email.attach_alternative(html, "text/html")
    email.send(fail_silently=False)
    logger.info("Email verification message sent", extra={"user_id": str(user.id)})
