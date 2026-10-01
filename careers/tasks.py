import os
import re

from celery import shared_task
from django.conf import settings

from accounts.tasks import _send_template
from careers.models import Application, PartnerApplication

COMPANY_LABELS = {
    "companyName": "Company name",
    "rcNumber": "RC number",
    "companyEmail": "Email",
    "phoneNumber": "Phone",
    "businessAddress": "Business address",
    "contactPerson": "Contact person",
}


def humanize(key):
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return words[:1].upper() + words[1:]


def _filename(stored_file):
    return os.path.basename(stored_file.name)


def _application_email(application):
    rows = [
        ("Pathway", application.get_pathway_display()),
        ("Full name", application.full_name),
        ("Email", application.email),
        ("Phone", application.phone),
        ("Country", application.country),
        ("State", application.state),
        ("City", application.city),
        ("LinkedIn", application.linkedin),
        ("Portfolio", application.portfolio),
        *((humanize(key), value) for key, value in application.answers.items()),
    ]
    documents = [
        (label, _filename(stored_file))
        for label, stored_file in [
            ("Resume / CV", application.resume),
            ("Professional headshot", application.headshot),
            ("Company profile", application.company_profile),
        ]
        if stored_file
    ]
    return f"New application: {application.reference_id}", rows, documents


def _partner_application_email(application):
    rows = [(label, application.company.get(key, "")) for key, label in COMPANY_LABELS.items()]
    rows.append((
        "Agreements signed",
        "; ".join(f"{item['key']} ({item['signedName']}, {item['signedAt']})" for item in application.agreements),
    ))
    documents = [(humanize(document.key), _filename(document.file)) for document in application.documents.all()]
    return f"New logistics partner application: {application.application_id}", rows, documents


@shared_task(name="careers.send_application_notification")
def send_application_notification(kind, pk, admin_url):
    if kind == "partner-application":
        subject, rows, documents = _partner_application_email(PartnerApplication.objects.get(pk=pk))
    else:
        subject, rows, documents = _application_email(Application.objects.get(pk=pk))

    _send_template(
        recipient=settings.CAREERS_NOTIFY_EMAIL,
        subject=subject,
        template="careers/application_notification.html",
        context={
            "subject": subject,
            "rows": [(label, value) for label, value in rows if str(value).strip()],
            "documents": documents,
            "admin_url": admin_url,
        },
    )
