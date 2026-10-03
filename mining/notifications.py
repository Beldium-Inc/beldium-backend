"""Tell the mining compliance desk when a miner files a new application."""
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

from organisations.models import OrganisationMembership, OrganisationType

logger = logging.getLogger(__name__)


def desk_recipients():
    """Addresses of every verified compliance-partner organisation.

    The organisation's own contact address when it has one, otherwise the
    addresses of its active members, so an application is never filed into
    silence just because a partner left the organisation email blank.
    """
    partners = OrganisationType.COMPLIANCE_PARTNER
    from organisations.models import Organisation

    addresses = []
    for org in Organisation.objects.filter(organisation_type=partners, verification_status="verified"):
        if org.email:
            addresses.append(org.email)
            continue
        members = OrganisationMembership.objects.filter(organisation=org, is_active=True).select_related("user")
        addresses.extend(m.user.email for m in members if m.user.email)
    return sorted({a.strip().lower() for a in addresses if a and a.strip()})


def _send(application_id, reference, site_name, mineral, organisation_name, recipients):
    link = f"{settings.FRONTEND_URL.rstrip('/')}/mining/applications"
    subject = f"New mining application {reference}"
    lines = [
        f"A new application {reference} was submitted"
        + (f" by {organisation_name}" if organisation_name else "")
        + ".",
        f"Site: {site_name or 'Not named'}",
        f"Mineral: {mineral or 'Not specified'}",
        "The first compliance partner to claim it reviews it.",
        f"Open the applications queue: {link}",
    ]
    html = "".join(f"<p>{escape(line)}</p>" for line in lines[:-1]) + f'<p><a href="{escape(link)}">Open the applications queue</a></p>'
    for recipient in recipients:
        try:
            message = EmailMultiAlternatives(
                subject=subject, body="\n".join(lines), from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
            )
            message.attach_alternative(html, "text/html")
            message.send(fail_silently=False)
        except Exception:
            logger.exception("Unable to email the mining desk", extra={"application_id": application_id})


def notify_desk_of_application(application):
    """Email the desk off the request thread; a failed send is only logged."""
    recipients = desk_recipients()
    if not recipients:
        return None
    organisation = application.organisation or getattr(application.site, "organisation", None)
    args = (
        str(application.id), application.reference, application.site_name, application.mineral,
        organisation.name if organisation else "", recipients,
    )

    def run():
        from django.db import close_old_connections

        try:
            _send(*args)
        finally:
            close_old_connections()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread
