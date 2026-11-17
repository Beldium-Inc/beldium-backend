"""Tell the mining compliance desk when a miner files a new application."""
from organisations.desk_alerts import alert_desk


def notify_desk_of_application(application):
    """Email the mining desk off the request thread; a failed send is only logged."""
    organisation = application.organisation or getattr(application.site, "organisation", None)
    reference = application.reference
    return alert_desk(
        "mining",
        f"New mining application {reference}",
        [
            f"A new application {reference} was submitted"
            + (f" by {organisation.name}" if organisation else "")
            + ".",
            f"Site: {application.site_name or 'Not named'}",
            f"Mineral: {application.mineral or 'Not specified'}",
            "The first compliance partner to claim it reviews it.",
        ],
        log_extra={"application_id": str(application.id)},
    )
