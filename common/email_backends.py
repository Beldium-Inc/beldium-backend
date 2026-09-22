import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

RESEND_API_URL = "https://api.resend.com/emails"


class ResendAPIEmailBackend(BaseEmailBackend):
    """Sends via Resend's HTTPS API instead of its SMTP relay.

    Render's outbound connection to smtp.resend.com repeatedly times out or
    hangs (seen in production well past EMAIL_TIMEOUT, sometimes 100+s),
    while a plain HTTPS request on port 443 doesn't have this problem — SMTP
    egress being flakier than HTTPS on a PaaS host is a common enough pattern
    that it's worth routing around entirely rather than tuning timeouts
    further. Uses EMAIL_HOST_PASSWORD as the API key: it's already the
    Resend credential in this project (see core/settings.py), so this needs
    no new setting.
    """

    def send_messages(self, email_messages):
        api_key = getattr(settings, "EMAIL_HOST_PASSWORD", "")
        if not api_key:
            if not self.fail_silently:
                raise ValueError("EMAIL_HOST_PASSWORD (Resend API key) is not set.")
            return 0

        sent_count = 0
        for message in email_messages:
            html_body = None
            for content, mimetype in getattr(message, "alternatives", []):
                if mimetype == "text/html":
                    html_body = content
                    break

            payload = {
                "from": message.from_email,
                "to": message.to,
                "subject": message.subject,
                "text": message.body,
            }
            if html_body:
                payload["html"] = html_body
            if message.cc:
                payload["cc"] = message.cc
            if message.bcc:
                payload["bcc"] = message.bcc

            request = Request(
                RESEND_API_URL,
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    # Resend's API sits behind Cloudflare, which blocks the
                    # default urllib User-Agent as bot traffic (a bare 403,
                    # Cloudflare's own "error code: 1010", not from Resend).
                    "User-Agent": "beldium-backend/1.0 (+https://beldium.com)",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=getattr(settings, "EMAIL_TIMEOUT", 10)) as response:
                    response.read()
            except HTTPError as exc:
                if not self.fail_silently:
                    raise ConnectionError(
                        f"Resend API returned {exc.code}: {exc.read().decode(errors='replace')}"
                    ) from exc
                continue
            except Exception:
                if not self.fail_silently:
                    raise
                continue
            sent_count += 1

        return sent_count
