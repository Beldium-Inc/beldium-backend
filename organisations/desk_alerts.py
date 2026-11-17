"""Email a sector's compliance desk about something waiting on it.

The desk for a sector is every verified compliance-partner organisation that
registered for that sector, plus older partners that recorded no sector (the
same rule ``organisations.access.sector_allowed`` uses to let them in). A
partner working only the logistics register is not mailed about miners.

Sends run off the request thread and a failed send is only logged: an alert
that cannot be delivered must never fail the signup or submission behind it.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape

from accounts.models import User
from organisations import access
from organisations.models import Organisation, OrganisationMembership, OrganisationType

logger = logging.getLogger(__name__)

# The sector whose desk hears about a signup on each applicant portal. The
# compliance portal is where partners themselves sign up, so it has none.
SECTOR_FOR_PORTAL = {
    User.Portal.MINER: "mining",
    User.Portal.LOGISTICS: "logistics",
}

SECTOR_LABELS = {"mining": "mining", "logistics": "logistics"}

# Where the desk lands for each sector on the compliance frontend.
DESK_PATHS = {
    "mining": "/mining/applications",
    "logistics": "/logistics/operator/applications",
}


def desk_recipients(sector):
    """Addresses of every verified compliance partner working ``sector``.

    The organisation's own contact address when it has one, otherwise the
    addresses of its active members, so an alert is never sent into silence
    just because a partner left the organisation email blank.
    """
    partners = Organisation.objects.filter(
        organisation_type=OrganisationType.COMPLIANCE_PARTNER, verification_status="verified",
    ).select_related("compliance_application").prefetch_related("sector_grants")

    addresses = []
    for org in partners:
        if not access.sector_allowed(org, sector):
            continue
        if org.email:
            addresses.append(org.email)
            continue
        members = OrganisationMembership.objects.filter(organisation=org, is_active=True).select_related("user")
        addresses.extend(m.user.email for m in members if m.user.is_active and m.user.email)
    return sorted({a.strip().lower() for a in addresses if a and a.strip()})


def desk_link(sector):
    return f"{settings.FRONTEND_URL.rstrip('/')}{DESK_PATHS.get(sector, '/')}"


def _send(subject, lines, link, link_label, recipients, log_extra):
    body = "\n".join([*lines, f"{link_label}: {link}"])
    html = "".join(f"<p>{escape(line)}</p>" for line in lines) + f'<p><a href="{escape(link)}">{escape(link_label)}</a></p>'
    for recipient in recipients:
        try:
            message = EmailMultiAlternatives(
                subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[recipient],
            )
            message.attach_alternative(html, "text/html")
            message.send(fail_silently=False)
        except Exception:
            logger.exception("Unable to email the compliance desk", extra=log_extra)


def alert_desk(sector, subject, lines, *, link_label="Open the applications queue", log_extra=None):
    """Email ``sector``'s desk in the background. Returns the thread, or None
    when the sector has no verified partner to tell (mainly for tests)."""
    recipients = desk_recipients(sector)
    if not recipients:
        logger.info("No verified compliance partner to alert", extra={"sector": sector, **(log_extra or {})})
        return None
    args = (subject, list(lines), desk_link(sector), link_label, recipients, log_extra or {})

    def run():
        from django.db import close_old_connections

        try:
            _send(*args)
        finally:
            close_old_connections()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def notify_desk_of_signup(user):
    """Tell the desk a new miner or logistics operator verified their account."""
    sector = SECTOR_FOR_PORTAL.get(user.portal)
    if not sector:
        return None
    name = " ".join(part for part in (user.first_name, user.last_name) if part) or user.email
    label = SECTOR_LABELS[sector]
    return alert_desk(
        sector,
        f"New {label} sign-up: {name}",
        [
            f"{name} ({user.email}) just created and verified a {label} account on Beldium.",
            "They have not filed an application yet; you will get another email when they do.",
        ],
        link_label="Open the desk",
        log_extra={"user_id": str(user.id)},
    )
